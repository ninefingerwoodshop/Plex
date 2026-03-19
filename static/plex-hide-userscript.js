// ==UserScript==
// @name         Plex Content Filter - Hide Shows/Movies
// @namespace    plex-content-filter
// @version      1.0
// @description  Adds "Hide from [user]" options to the Plex context menu
// @match        http://localhost:32400/web/*
// @match        https://app.plex.tv/*
// @grant        GM_xmlhttpRequest
// @connect      localhost
// ==/UserScript==

(function () {
    'use strict';

    // ---- CONFIG ----
    const FILTER_API = 'http://localhost:5050';
    const PLEX_TOKEN = '9AemmEzDpzYae8rrRHsZ';
    // Which users to show in the hide menu (lowercase)
    const USERS = ['gibbens', 'melinda', 'jack', 'kate', 'patricia', 'bizzie'];
    // ----------------

    const STYLE_ID = 'plex-filter-styles';

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
            .plex-filter-item {
                position: relative;
            }
            .plex-filter-item:hover > .plex-filter-submenu {
                display: block;
            }
            .plex-filter-submenu {
                display: none;
                position: absolute;
                left: 100%;
                top: 0;
                background: #3f3f3f;
                border-radius: 6px;
                padding: 4px 0;
                min-width: 160px;
                box-shadow: 0 8px 24px rgba(0,0,0,0.5);
                z-index: 99999;
            }
            .plex-filter-submenu button {
                display: block;
                width: 100%;
                padding: 8px 16px;
                background: none;
                border: none;
                color: #eee;
                font-size: 13px;
                text-align: left;
                cursor: pointer;
                white-space: nowrap;
            }
            .plex-filter-submenu button:hover {
                background: #4a4a4a;
            }
            .plex-filter-submenu button.is-hidden {
                color: #e5a00d;
            }
            .plex-filter-submenu button.is-hidden::before {
                content: "\\2715 ";
            }
            .plex-filter-toast {
                position: fixed;
                bottom: 24px;
                left: 50%;
                transform: translateX(-50%) translateY(80px);
                background: #1a1a1a;
                border: 1px solid #e5a00d;
                color: #e5a00d;
                padding: 10px 24px;
                border-radius: 8px;
                font-size: 13px;
                z-index: 999999;
                transition: transform 0.3s ease;
                pointer-events: none;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }
            .plex-filter-toast.show {
                transform: translateX(-50%) translateY(0);
            }
            .plex-filter-toast.error {
                border-color: #e74c3c;
                color: #e74c3c;
            }
        `;
        document.head.appendChild(style);
    }

    function showToast(msg, isError) {
        let toast = document.querySelector('.plex-filter-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.className = 'plex-filter-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.classList.toggle('error', !!isError);
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2500);
    }

    function getRatingKeyFromUrl() {
        // Plex URLs look like: /web/index.html#!/server/.../details?key=%2Flibrary%2Fmetadata%2F34215
        const match = window.location.href.match(/metadata%2F(\d+)/);
        if (match) return match[1];
        return null;
    }

    function getRatingKeyFromContext() {
        // Try to find the ratingKey from the currently focused/selected item
        // Plex stores data attributes on poster cards
        const selected = document.querySelector('[class*="isSelected"]') ||
                         document.querySelector('[class*="CardOverlay-isHovered"]');
        if (selected) {
            const link = selected.closest('a[href*="metadata"]') || selected.querySelector('a[href*="metadata"]');
            if (link) {
                const m = link.href.match(/metadata\/(\d+)/);
                if (m) return m[1];
            }
        }
        return null;
    }

    function getMediaTypeFromPage() {
        // Determine if we're looking at shows or movies
        const url = window.location.href;
        if (url.includes('section=3') || url.includes('tv') || document.title.toLowerCase().includes('tv')) {
            return 'show';
        }
        // Check page content
        const header = document.querySelector('[class*="PageHeaderTitle"]');
        if (header) {
            const text = header.textContent.toLowerCase();
            if (text.includes('tv') || text.includes('show')) return 'show';
            if (text.includes('movie')) return 'movie';
        }
        return 'show'; // default
    }

    function findRatingKey() {
        // Strategy 1: from the context menu's parent/trigger element
        // Plex context menus are triggered from poster cards that have links with metadata IDs
        const popovers = document.querySelectorAll('[class*="PopupMenu"], [class*="ContextMenu"], [class*="popover"]');

        // Strategy 2: look for the most recently hovered/right-clicked card
        // Plex cards have links like /server/{id}/details?key=%2Flibrary%2Fmetadata%2F12345
        const allLinks = document.querySelectorAll('a[href*="metadata%2F"], a[href*="metadata/"]');

        // Try to get from the item that has a visible overlay (the one being right-clicked)
        const overlays = document.querySelectorAll('[class*="isOpen"], [class*="isActive"], [class*="isSelected"]');
        for (const el of overlays) {
            const container = el.closest('[class*="Card"]') || el.closest('[class*="cell"]') || el.parentElement;
            if (container) {
                const link = container.querySelector('a[href*="metadata"]');
                if (link) {
                    const m = link.href.match(/metadata(?:%2F|\/)(\d+)/);
                    if (m) return m[1];
                }
            }
        }

        // Strategy 3: if on a detail page, get from URL
        return getRatingKeyFromUrl();
    }

    async function fetchHiddenState(ratingKey) {
        // Check which users have this item hidden by reading its labels
        try {
            const resp = await fetch(`http://localhost:32400/library/metadata/${ratingKey}?X-Plex-Token=9AemmEzDpzYae8rrRHsZ`, {
                headers: { 'Accept': 'application/json' }
            });
            const data = await resp.json();
            const labels = (data.MediaContainer.Metadata[0].Label || []).map(l => l.tag.toLowerCase());
            const hiddenMap = {};
            for (const user of USERS) {
                hiddenMap[user] = labels.includes(`hide-${user}`);
            }
            return hiddenMap;
        } catch (e) {
            return null;
        }
    }

    async function toggleHide(username, ratingKey, mediaType) {
        try {
            const resp = await fetch(`${FILTER_API}/api/filter/toggle`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Filter-Token': PLEX_TOKEN,
                },
                body: JSON.stringify({
                    username: username,
                    ratingKey: ratingKey,
                    mediaType: mediaType,
                }),
            });
            const data = await resp.json();
            if (data.error) {
                showToast('Error: ' + data.error, true);
                return null;
            }
            return data.hidden;
        } catch (e) {
            showToast('Cannot reach filter API at ' + FILTER_API, true);
            return null;
        }
    }

    function findContextMenu() {
        // Plex's context menu uses specific class patterns
        // Look for the popup/dropdown that just appeared
        const menus = document.querySelectorAll('[class*="Menu-menuContainer"], [class*="PopupMenu"], [class*="ContextualMenu"]');
        for (const menu of menus) {
            if (menu.offsetParent !== null) return menu; // visible
        }
        // Fallback: look for any visible menu-like element with the right items
        const all = document.querySelectorAll('[role="menu"], [class*="menuContainer"]');
        for (const el of all) {
            if (el.offsetParent !== null && el.querySelector('[class*="MenuItem"]')) return el;
        }
        return null;
    }

    let lastInjectedMenu = null;
    let lastRatingKey = null;

    // Track the last right-clicked/long-pressed element to find the ratingKey
    let lastContextTarget = null;
    document.addEventListener('contextmenu', (e) => {
        lastContextTarget = e.target;
    }, true);
    // Also track Plex's custom "..." button clicks
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[class*="MoreButton"], [class*="moreButton"], [data-testid*="more"]');
        if (btn) lastContextTarget = btn;
    }, true);

    function getRatingKeyFromTarget(target) {
        if (!target) return null;
        // Walk up to find a card/cell container with a metadata link
        let el = target;
        for (let i = 0; i < 15; i++) {
            if (!el) break;
            const link = el.querySelector ? el.querySelector('a[href*="metadata"]') : null;
            if (link) {
                const m = link.href.match(/metadata(?:%2F|\/)(\d+)/);
                if (m) return m[1];
            }
            // Also check the element itself if it's a link
            if (el.href) {
                const m = el.href.match(/metadata(?:%2F|\/)(\d+)/);
                if (m) return m[1];
            }
            el = el.parentElement;
        }
        return null;
    }

    async function injectMenuItems(menu) {
        if (menu === lastInjectedMenu) return;
        lastInjectedMenu = menu;

        // Find the ratingKey for the item this menu belongs to
        let ratingKey = getRatingKeyFromTarget(lastContextTarget) || findRatingKey();
        if (!ratingKey) {
            // Last resort: check URL
            ratingKey = getRatingKeyFromUrl();
        }
        if (!ratingKey) return;
        lastRatingKey = ratingKey;

        const mediaType = getMediaTypeFromPage();

        // Fetch current hidden state from Plex labels
        const hiddenMap = await fetchHiddenState(ratingKey);
        if (!hiddenMap) return;

        // Check if menu is still visible
        if (!menu.offsetParent) return;

        // Find the list of menu items
        const menuList = menu.querySelector('ul, [role="menu"], [class*="menuItems"]') || menu;

        // Check if we already injected
        if (menu.querySelector('.plex-filter-item')) return;

        // Find a reference menu item to clone styling
        const existingItem = menuList.querySelector('li, [class*="MenuItem"]');
        if (!existingItem) return;

        // Create separator
        const sep = document.createElement('div');
        sep.style.cssText = 'height:1px; background:#555; margin:4px 0;';

        // Create the "Hide from..." parent item
        const filterItem = document.createElement('li');
        filterItem.className = (existingItem.className || '') + ' plex-filter-item';
        filterItem.style.position = 'relative';

        // Clone the style of an existing menu button
        const existingBtn = existingItem.querySelector('button, a, [role="menuitem"]');

        const mainBtn = document.createElement('button');
        if (existingBtn) {
            mainBtn.className = existingBtn.className;
        }
        mainBtn.style.cssText = 'width:100%; text-align:left; cursor:pointer; display:flex; align-items:center; justify-content:space-between;';
        mainBtn.innerHTML = '<span>Hide from...</span><span style="font-size:10px; opacity:0.6;">\u25B6</span>';

        // Build submenu
        const submenu = document.createElement('div');
        submenu.className = 'plex-filter-submenu';

        for (const user of USERS) {
            const btn = document.createElement('button');
            const isHidden = hiddenMap[user];
            btn.textContent = isHidden ? `Unhide from ${user}` : `Hide from ${user}`;
            if (isHidden) btn.classList.add('is-hidden');
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                btn.textContent = 'Updating...';
                btn.disabled = true;
                const result = await toggleHide(user, ratingKey, mediaType);
                if (result !== null) {
                    const action = result ? 'Hidden' : 'Visible';
                    showToast(`${action} for ${user}`);
                    btn.textContent = result ? `Unhide from ${user}` : `Hide from ${user}`;
                    btn.classList.toggle('is-hidden', result);
                }
                btn.disabled = false;
                // Close the menu
                document.dispatchEvent(new Event('click'));
            });
            submenu.appendChild(btn);
        }

        filterItem.appendChild(mainBtn);
        filterItem.appendChild(submenu);

        menuList.appendChild(sep);
        menuList.appendChild(filterItem);
    }

    // Watch for context menus appearing
    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== 1) continue;
                // Check if this is a menu or contains a menu
                const menu = node.querySelector ?
                    (node.matches('[class*="Menu"], [class*="Popup"], [role="menu"]') ? node : node.querySelector('[class*="Menu-menuContainer"], [class*="PopupMenu"], [role="menu"]'))
                    : null;
                if (menu) {
                    injectMenuItems(menu);
                    return;
                }
                // Check the node itself
                if (node.classList && (
                    [...node.classList].some(c => c.includes('Menu') || c.includes('Popup') || c.includes('menu'))
                )) {
                    injectMenuItems(node);
                    return;
                }
            }
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    injectStyles();

    console.log('[Plex Content Filter] Userscript loaded. Users:', USERS.join(', '));
})();
