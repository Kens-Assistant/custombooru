"use strict";

const misc = require("./util/misc.js");
const TagCategoryList = require("./models/tag_category_list.js");

let _stylesheet = null;

function refreshCategoryColorMap() {
    return TagCategoryList.get().then((response) => {
        if (_stylesheet) {
            document.head.removeChild(_stylesheet);
        }
        _stylesheet = document.createElement("style");
        document.head.appendChild(_stylesheet);
        for (let category of response.results) {
            const ruleName = misc.makeCssName(category.name, "tag");
            _stylesheet.sheet.insertRule(
                `.${ruleName} { color: ${category.color} }`,
                _stylesheet.sheet.cssRules.length
            );
        }

        // Keep default tags legible regardless of category color configuration.
        _stylesheet.sheet.insertRule(
            `.tag-default { color: black !important }`,
            _stylesheet.sheet.cssRules.length
        );
        _stylesheet.sheet.insertRule(
            `body.darktheme .tag-default { color: white !important }`,
            _stylesheet.sheet.cssRules.length
        );
    });
}

module.exports = {
    refreshCategoryColorMap: refreshCategoryColorMap,
};
