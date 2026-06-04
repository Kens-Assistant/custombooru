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
            // Apply category colors to non-anchor elements and to anchor
            // elements with higher precedence so tag links keep their
            // configured color even if a global `a` rule appears later.
            _stylesheet.sheet.insertRule(
                `.${ruleName}:not(a) { color: ${category.color} }`,
                _stylesheet.sheet.cssRules.length
            );
            _stylesheet.sheet.insertRule(
                `a.${ruleName} { color: ${category.color} !important }`,
                _stylesheet.sheet.cssRules.length
            );
                // Only force white in dark theme for categories whose configured
                // color is too dark to be legible on a dark background.
                try {
                    const hex = category.color.trim();
                    const m = hex.match(/^#?([0-9a-fA-F]{6})$/);
                    if (m) {
                        const r = parseInt(m[1].substr(0,2), 16);
                        const g = parseInt(m[1].substr(2,2), 16);
                        const b = parseInt(m[1].substr(4,2), 16);
                        // relative luminance approximation
                        const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
                        if (lum < 0.2) {
                            // For very-dark configured colors, force white for
                            // anchors in darktheme so they remain legible.
                            _stylesheet.sheet.insertRule(
                                `body.darktheme a.${ruleName} { color: white !important }`,
                                _stylesheet.sheet.cssRules.length
                            );
                        }
                    }
                } catch (e) {
                    // If parsing fails, do not forcibly override category color.
                }
        }

        // Keep default tags legible regardless of category color configuration.
        // Use inherited color so the tag respects theme text color instead of
        // forcing a literal black, which can be invisible on dark backgrounds.
        // _stylesheet.sheet.insertRule(
        //     `.tag-default { color: inherit !important }`,
        //     _stylesheet.sheet.cssRules.length
        // );
        // _stylesheet.sheet.insertRule(
        //     `body.darktheme a.tag-default { color: inherit !important }`,
        //     _stylesheet.sheet.cssRules.length
        // );
    });
}

module.exports = {
    refreshCategoryColorMap: refreshCategoryColorMap,
};
