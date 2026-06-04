// Ensure non-tag/pool anchor colors in darktheme remain readable
(function(){
    function inject() {
        try {
            const css = `body.darktheme a:not([class^="tag-"]):not([class*=" tag-"]):not([class^="pool-"]):not([class*=" pool-"]){color:#e6e6e6!important;text-decoration:underline!important}
body.darktheme .post-view .readonly-sidebar .details .source a,body.darktheme .post-view .readonly-sidebar .details .search a{color:#e6e6e6!important;text-decoration:underline!important}`;
            const s = document.createElement('style');
            s.setAttribute('data-generated','anchor-overrides');
            s.appendChild(document.createTextNode(css));
            document.head.appendChild(s);
        } catch (e) {
            // fail silently
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
