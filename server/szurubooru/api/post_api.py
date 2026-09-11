from datetime import datetime
from typing import Dict, List, Optional

from szurubooru import db, errors, model, rest, search
from szurubooru.func import (
    auth,
    autotagger,
    favorites,
    mime,
    pools,
    posts,
    scores,
    serialization,
    snapshots,
    tags,
    versions,
)

_search_executor_config = search.configs.PostSearchConfig()
_search_executor = search.Executor(_search_executor_config)


def _get_post_id(params: Dict[str, str]) -> int:
    try:
        return int(params["post_id"])
    except TypeError:
        raise posts.InvalidPostIdError(
            "Invalid post ID: %r." % params["post_id"]
        )


def _get_post(params: Dict[str, str]) -> model.Post:
    return posts.get_post_by_id(_get_post_id(params))


def _serialize_post(
    ctx: rest.Context, post: Optional[model.Post]
) -> rest.Response:
    return posts.serialize_post(
        post, ctx.user, options=serialization.get_serialization_options(ctx)
    )


@rest.routes.get("/posts/?")
def get_posts(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:list")
    _search_executor_config.user = ctx.user
    return _search_executor.execute_and_serialize(
        ctx, lambda post: _serialize_post(ctx, post)
    )


@rest.routes.post("/posts/?")
def create_post(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    anonymous = ctx.get_param_as_bool("anonymous", default=False)
    if anonymous:
        auth.verify_privilege(ctx.user, "posts:create:anonymous")
    else:
        auth.verify_privilege(ctx.user, "posts:create:identified")
    content = ctx.get_file(
        "content",
        use_video_downloader=auth.has_privilege(
            ctx.user, "uploads:use_downloader"
        ),
    )
    tag_names = ctx.get_param_as_string_list("tags", default=[])
    safety = ctx.get_param_as_string("safety")
    source = ctx.get_param_as_string("source", default="")
    if ctx.has_param("contentUrl") and not source:
        source = ctx.get_param_as_string("contentUrl", default="")
    relations = ctx.get_param_as_int_list("relations", default=[])
    notes = ctx.get_param_as_list("notes", default=[])
    flags = ctx.get_param_as_string_list(
        "flags", default=posts.get_default_flags(content)
    )

    post, new_tags = posts.create_post(
        content, tag_names, None if anonymous else ctx.user
    )
    if len(new_tags):
        auth.verify_privilege(ctx.user, "tags:create")
    posts.update_post_safety(post, safety)
    posts.update_post_source(post, source)
    posts.update_post_relations(post, relations)
    posts.update_post_notes(post, notes)
    posts.update_post_flags(post, flags)
    if ctx.has_file("thumbnail"):
        posts.update_post_thumbnail(post, ctx.get_file("thumbnail"))
    ctx.session.add(post)
    ctx.session.flush()
    create_snapshots_for_post(post, new_tags, None if anonymous else ctx.user)
    alternate_format_posts = posts.generate_alternate_formats(post, content)
    for alternate_post, alternate_post_new_tags in alternate_format_posts:
        create_snapshots_for_post(
            alternate_post,
            alternate_post_new_tags,
            None if anonymous else ctx.user,
        )
    ctx.session.commit()
    return _serialize_post(ctx, post)


def create_snapshots_for_post(
    post: model.Post, new_tags: List[model.Tag], user: Optional[model.User]
):
    snapshots.create(post, user)
    for tag in new_tags:
        snapshots.create(tag, user)


@rest.routes.post("/post/(?P<post_id>[^/]+)/tag-suggestions/?")
def get_tag_suggestions(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:edit:tags")
    post = _get_post(params)
    suggestions = autotagger.suggest(post)
    return {"suggestions": suggestions}


@rest.routes.post("/posts/gallery/?")
def create_gallery(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    """
    Create multiple posts in order and wrap them in a pool (gallery).

    Request body (JSON):
    {
        "items": [
            {
                "contentUrl": "https://...",
                "tags": ["tag1"],
                "safety": "safe",
                "source": "https://...",
                "flags": []
            },
            ...
        ],
        "poolName": "Gallery title",
        "poolCategory": "gallery",   // optional, falls back to default
        "tags": ["shared_tag"],      // applied to every item
        "safety": "safe",            // default for items that omit it
        "source": "https://..."      // default source for all items
    }

    Returns:
    {
        "pool": <pool resource>,
        "posts": [<post resource>, ...]
    }
    """
    anonymous = ctx.get_param_as_bool("anonymous", default=False)
    if anonymous:
        auth.verify_privilege(ctx.user, "posts:create:anonymous")
    else:
        auth.verify_privilege(ctx.user, "posts:create:identified")

    items = ctx.get_param_as_list("items", default=[])
    if not items:
        raise posts.InvalidPostSourceError("Gallery must contain at least one item.")

    pool_name = ctx.get_param_as_string("poolName", default="")
    if not pool_name:
        raise pools.InvalidPoolNameError("poolName is required for gallery upload.")

    pool_category = ctx.get_param_as_string("poolCategory", default="")
    shared_tag_names = ctx.get_param_as_string_list("tags", default=[])
    default_safety = ctx.get_param_as_string("safety", default="safe")
    default_source = ctx.get_param_as_string("source", default="")

    uploader = None if anonymous else ctx.user
    created_posts = []
    all_new_tags = []

    for item in items:
        if not isinstance(item, dict):
            raise posts.InvalidPostSourceError("Each gallery item must be an object.")
        content_url = item.get("contentUrl") or item.get("source", "")
        if not content_url:
            raise posts.InvalidPostSourceError("Each gallery item needs a contentUrl.")

        try:
            content = ctx.context.get("_net_download", None)
            from szurubooru.func import net as _net
            content = _net.download(
                content_url,
                use_video_downloader=auth.has_privilege(
                    ctx.user, "uploads:use_downloader"
                ),
            )
        except Exception as exc:
            raise posts.InvalidPostSourceError(
                f"Could not download gallery item: {exc}"
            )

        item_tag_names = list(shared_tag_names) + list(
            item.get("tags") or []
        )
        item_safety = item.get("safety") or default_safety
        item_source = item.get("source") or default_source or content_url
        item_flags = item.get("flags") or posts.get_default_flags(content)

        post, new_tags = posts.create_post(content, item_tag_names, uploader)
        if new_tags:
            auth.verify_privilege(ctx.user, "tags:create")
        posts.update_post_safety(post, item_safety)
        posts.update_post_source(post, item_source)
        posts.update_post_flags(post, item_flags)
        ctx.session.add(post)
        ctx.session.flush()
        create_snapshots_for_post(post, new_tags, uploader)
        created_posts.append(post)
        all_new_tags.extend(new_tags)

    # Resolve pool category — use provided or fall back to site default
    from szurubooru.func import pool_categories as _pool_cats
    if pool_category:
        try:
            _pool_cats.get_category_by_name(pool_category)
        except Exception:
            pool_category = _pool_cats.get_default_category_name()
    else:
        pool_category = _pool_cats.get_default_category_name()

    pool = pools.create_pool(
        names=[pool_name],
        category_name=pool_category,
        post_ids=[p.post_id for p in created_posts],
    )
    pool.description = default_source or ""
    ctx.session.add(pool)
    ctx.session.flush()
    snapshots.create(pool, uploader)
    ctx.session.commit()

    return {
        "pool": pools.serialize_pool(pool),
        "posts": [_serialize_post(ctx, p) for p in created_posts],
    }


@rest.routes.get("/post/(?P<post_id>[^/]+)/?")
def get_post(ctx: rest.Context, params: Dict[str, str]) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:view")
    post = _get_post(params)
    return _serialize_post(ctx, post)


@rest.routes.put("/post/(?P<post_id>[^/]+)/?")
def update_post(ctx: rest.Context, params: Dict[str, str]) -> rest.Response:
    post = _get_post(params)
    versions.verify_version(post, ctx)
    versions.bump_version(post)
    if ctx.has_file("content"):
        auth.verify_privilege(ctx.user, "posts:edit:content")
        posts.update_post_content(
            post,
            ctx.get_file(
                "content",
                use_video_downloader=auth.has_privilege(
                    ctx.user, "uploads:use_downloader"
                ),
            ),
        )
    if ctx.has_param("tags"):
        auth.verify_privilege(ctx.user, "posts:edit:tags")
        new_tags = posts.update_post_tags(
            post, ctx.get_param_as_string_list("tags")
        )
        if len(new_tags):
            auth.verify_privilege(ctx.user, "tags:create")
            db.session.flush()
            for tag in new_tags:
                snapshots.create(tag, ctx.user)
    if ctx.has_param("safety"):
        auth.verify_privilege(ctx.user, "posts:edit:safety")
        posts.update_post_safety(post, ctx.get_param_as_string("safety"))
    if ctx.has_param("source"):
        auth.verify_privilege(ctx.user, "posts:edit:source")
        posts.update_post_source(post, ctx.get_param_as_string("source"))
    elif ctx.has_param("contentUrl"):
        posts.update_post_source(post, ctx.get_param_as_string("contentUrl"))
    if ctx.has_param("relations"):
        auth.verify_privilege(ctx.user, "posts:edit:relations")
        posts.update_post_relations(
            post, ctx.get_param_as_int_list("relations")
        )
    if ctx.has_param("notes"):
        auth.verify_privilege(ctx.user, "posts:edit:notes")
        posts.update_post_notes(post, ctx.get_param_as_list("notes"))
    if ctx.has_param("flags"):
        auth.verify_privilege(ctx.user, "posts:edit:flags")
        posts.update_post_flags(post, ctx.get_param_as_string_list("flags"))
    if ctx.has_file("thumbnail"):
        auth.verify_privilege(ctx.user, "posts:edit:thumbnail")
        posts.update_post_thumbnail(post, ctx.get_file("thumbnail"))
    post.last_edit_time = datetime.utcnow()
    ctx.session.flush()
    snapshots.modify(post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.delete("/post/(?P<post_id>[^/]+)/?")
def delete_post(ctx: rest.Context, params: Dict[str, str]) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:delete")
    post = _get_post(params)
    versions.verify_version(post, ctx)
    snapshots.delete(post, ctx.user)
    posts.delete(post)
    ctx.session.commit()
    return {}


@rest.routes.post("/post-merge/?")
def merge_posts(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    source_post_id = ctx.get_param_as_int("remove")
    target_post_id = ctx.get_param_as_int("mergeTo")
    source_post = posts.get_post_by_id(source_post_id)
    target_post = posts.get_post_by_id(target_post_id)
    replace_content = ctx.get_param_as_bool("replaceContent")
    versions.verify_version(source_post, ctx, "removeVersion")
    versions.verify_version(target_post, ctx, "mergeToVersion")
    versions.bump_version(target_post)
    auth.verify_privilege(ctx.user, "posts:merge")
    posts.merge_posts(source_post, target_post, replace_content)
    snapshots.merge(source_post, target_post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, target_post)


@rest.routes.get("/featured-post/?")
def get_featured_post(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:view:featured")
    post = posts.try_get_featured_post()
    return _serialize_post(ctx, post)


@rest.routes.post("/featured-post/?")
def set_featured_post(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:feature")
    post_id = ctx.get_param_as_int("id")
    post = posts.get_post_by_id(post_id)
    featured_post = posts.try_get_featured_post()
    if featured_post and featured_post.post_id == post.post_id:
        raise posts.PostAlreadyFeaturedError(
            "Post %r is already featured." % post_id
        )
    posts.feature_post(post, ctx.user)
    snapshots.modify(post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.put("/post/(?P<post_id>[^/]+)/score/?")
def set_post_score(ctx: rest.Context, params: Dict[str, str]) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:score")
    post = _get_post(params)
    score = ctx.get_param_as_int("score")
    scores.set_score(post, ctx.user, score)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.delete("/post/(?P<post_id>[^/]+)/score/?")
def delete_post_score(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:score")
    post = _get_post(params)
    scores.delete_score(post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.post("/post/(?P<post_id>[^/]+)/favorite/?")
def add_post_to_favorites(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:favorite")
    post = _get_post(params)
    favorites.set_favorite(post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.delete("/post/(?P<post_id>[^/]+)/favorite/?")
def delete_post_from_favorites(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:favorite")
    post = _get_post(params)
    favorites.unset_favorite(post, ctx.user)
    ctx.session.commit()
    return _serialize_post(ctx, post)


@rest.routes.get("/post/(?P<post_id>[^/]+)/around/?")
def get_posts_around(
    ctx: rest.Context, params: Dict[str, str]
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:list")
    _search_executor_config.user = ctx.user
    post_id = _get_post_id(params)
    return _search_executor.get_around_and_serialize(
        ctx, post_id, lambda post: _serialize_post(ctx, post)
    )


@rest.routes.post("/posts/reverse-search/?")
def get_posts_by_image(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    auth.verify_privilege(ctx.user, "posts:reverse_search")
    content = ctx.get_file("content")

    try:
        lookalikes = posts.search_by_image(content)
    except (errors.ThirdPartyError, errors.ProcessingError):
        lookalikes = []

    return {
        "exactPost": _serialize_post(
            ctx, posts.search_by_image_exact(content)
        ),
        "similarPosts": [
            {
                "distance": distance,
                "post": _serialize_post(ctx, post),
            }
            for distance, post in lookalikes
        ],
    }
