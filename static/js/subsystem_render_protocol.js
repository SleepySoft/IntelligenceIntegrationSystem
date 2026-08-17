/* static/js/subsystem_render_protocol.js
 * ??? UI ????????? + ????? + ????????
 * ? doc/20260817_subsystem_ui_plugin_rendering.md
 *
 * ???
 *   - ???????????? _config/subsystems/{name}/?ui_plugin.js ??????
 *     ?? window.SubsystemUI.register({...}) ???
 *   - ????? id??????defaults????????????????????????
 *   - ????? import subsystem/<block> or static/<block>?
 *         resolve(blockId) => plugin.blocks[blockId] || defaults[blockId]
 *   - ???? = ??????? renderCard/renderDetail = ?????/????
 *     ??? detailURL/onCardClick = ???????????????
 */
(function () {
    if (window.SubsystemUI) return;

    const plugins = {};          // name -> plugin descriptor

    // ------------------------------------------------------------------ register

    function register(plugin) {
        if (!plugin || !plugin.name) {
            console.error('[SubsystemUI] plugin must provide `name`.');
            return;
        }
        plugins[plugin.name] = plugin;
    }

    function getPlugin(name) {
        return (name && plugins[name]) || null;
    }

    function hasPlugin(name) {
        return !!getPlugin(name);
    }

    function resolve(plugin, blockId) {
        if (plugin && plugin.blocks && typeof plugin.blocks[blockId] === 'function') {
            return plugin.blocks[blockId];
        }
        return null;  // ??? -> ????????
    }

    // ------------------------------------------------------------------ ??
    // ??? ArticleRenderer.generateArticleCardHtml ?????????????????????
    // ??????fn(doc, ctx, helpers)?ctx ? ArticleRenderer ????? escapeHTML ???
    // doc: ?????????????????? ctx ???helpers ??????

    function buildDefaultCardFragments(doc, ctx, helpers) {
        const uuid = ctx.escapeHTML(doc.UUID || 'Unknown-UUID');
        const base = helpers.base || '';
        const intelUrl = base + '/intelligence/' + (doc.UUID || 'Unknown-UUID');
        const appendix = doc.APPENDIX || {};

        // ---- meta/time????? + ???????????? ----
        const raw_archived = appendix['__TIME_ARCHIVED__'] || '';
        let archived_html = '';
        if (raw_archived) {
            archived_html = `<span class="article-time archived-time" data-archived="${ctx.escapeHTML(raw_archived)}">Archived: ${ctx.formatLocalTime(raw_archived)}</span>`;
        }
        const pub_time_raw = appendix['__TIME_PUB__'] || doc.PUB_TIME || doc.pub_time || doc.collect_time;
        const time_block = `                ${archived_html}\n                <span class="article-time">Publish: ${ctx.formatLocalTime(pub_time_raw)}</span>`;

        // ---- meta/vector ----
        const vector_score = appendix['__VECTOR_SCORE__'];
        let vector_block = '                ';
        if (vector_score !== undefined && vector_score !== null) {
            const formattedScore = parseFloat(vector_score).toFixed(3);
            let badgeClass = vector_score >= 0.8 ? 'bg-success' :
                            (vector_score >= 0.6 ? 'bg-primary' :
                            (vector_score >= 0.4 ? 'bg-warning' : 'bg-danger'));
            vector_block = `                <span class="badge ${badgeClass} similarity-badge"><span class="similarity-score">${formattedScore}</span></span>`;
        }

        // ---- meta/source ----
        const informant_val = doc.INFORMANT || doc.informant || doc.source || '';
        const informant = ctx.escapeHTML(informant_val);
        const informant_html = ctx.isValidUrl(informant)
            ? `<a href="${informant}" target="_blank" class="source-link">${informant}</a>`
            : (informant || 'Unknown Source');
        const source_block = `                <span class="article-source">Source: ${informant_html}</span>`;

        // ---- title ----
        const title_text = ctx.escapeHTML(doc.EVENT_TITLE || doc.title || 'No Title');
        const title_block = `              <a href="${intelUrl}" class="article-title" data-uuid="${uuid}">\n                ${title_text}\n              </a>`;

        // ---- summary ----
        const summary_block = `            <p class="article-summary">${ctx.escapeHTML(doc.EVENT_BRIEF || 'No Brief')}</p>`;

        // ---- debug/left (v1/v2 ??) ----
        const prompt_version = appendix['__PROMPT_VERSION__'];
        const is_v2 = prompt_version && !isNaN(Number(prompt_version)) && Number(prompt_version) >= 20;
        let left_content = '';
        if (is_v2) {
            const taxonomy = ctx.escapeHTML(doc.TAXONOMY || 'Unclassified');
            const sub_categories = doc.SUB_CATEGORY || [];
            const total_score = appendix['__TOTAL_SCORE__'];
            let tags_html = '';
            if (Array.isArray(sub_categories) && sub_categories.length > 0) {
                tags_html = sub_categories.map(tag => `<span class="v2-category-tag">${ctx.escapeHTML(tag)}</span>`).join('');
            }
            const category_line = `<div style="margin-bottom: 4px; display: flex; align-items: center; gap: 6px;">\n                <span class="debug-label" style="color:#1a73e8; font-size:0.95rem;">${taxonomy}</span>\n                ${tags_html}\n            </div>`;
            let total_score_html = '';
            if (total_score !== undefined && total_score !== null) {
                total_score_html = `\n                <div class="article-rating" style="margin: 6px 0 4px 0;">\n                    <span class="debug-label">\u603b\u5206:</span>\n                    ${ctx.createRatingStars(total_score)}\n                </div>`;
            }
            left_content = `\n            ${category_line}\n            ${total_score_html}\n            <div>\n                <span class="debug-label">UUID:</span> ${uuid}\n            </div>`;
        } else {
            const max_rate_class = ctx.escapeHTML(appendix['__MAX_RATE_CLASS__'] || '');
            const max_rate_score = appendix['__MAX_RATE_SCORE__'];
            if (max_rate_class && max_rate_score !== null) {
                left_content += `\n                <div class="article-rating" style="margin-bottom: 4px;">\n                    <span class="debug-label">${max_rate_class}:</span>\n                    ${ctx.createRatingStars(max_rate_score)}\n                </div>`;
            }
            left_content += `\n            <div>\n                <span class="debug-label">UUID:</span> ${uuid}\n            </div>`;
        }
        const debug_left_block = left_content;

        // ---- debug/right ----
        let right_content = '';
        const ai_service = ctx.escapeHTML(appendix['__AI_SERVICE__'] || '');
        const ai_model = ctx.escapeHTML(appendix['__AI_MODEL__'] || '');
        if (ai_service || ai_model) {
            if (ai_service) right_content += `<div><span class="debug-label">Service:</span><span class="debug-value-truncate" title="${ai_service}">${ai_service}</span></div>`;
            if (ai_model) right_content += `<div><span class="debug-label">Model:</span><span class="debug-value-truncate" title="${ai_model}">${ai_model}</span></div>`;
        }
        if (is_v2 && prompt_version) {
            const pvEscaped = ctx.escapeHTML(prompt_version);
            right_content += `\n              <div>\n                <span class="debug-label">Prompt:</span>\n                <button\n                  type="button"\n                  class="prompt-link-btn"\n                  data-prompt-version="${pvEscaped}"\n                  title="Click to view prompt v${pvEscaped}"\n                >v${pvEscaped}</button>\n              </div>`;
        }
        const debug_right_block = right_content;

        return {
            'card/title': title_block,
            'card/meta/time': time_block,
            'card/meta/vector': vector_block,
            'card/meta/source': source_block,
            'card/summary': summary_block,
            'card/debug/left': debug_left_block,
            'card/debug/right': debug_right_block,
        };
    }

    // ???????????? generateArticleCardHtml ??????
    function composeCard(fragments) {
        return `
        <div class="article-card">
            <h3>
${fragments['card/title'] || ''}
            </h3>
            <div class="article-meta">
${fragments['card/meta/time'] || ''}
${fragments['card/meta/vector'] || ''}
${fragments['card/meta/source'] || ''}
            </div>
${fragments['card/summary'] || ''}

            <div class="debug-info">
                <div class="debug-left">
                    ${fragments['card/debug/left'] || ''}
                </div>
                <div class="debug-right">
                    ${fragments['card/debug/right'] || ''}
                </div>
            </div>
        </div>`;
    }

function buildCard(plugin, doc, ctx, helpers) {
        if (!ctx || typeof ctx.generateArticleCardHtml !== 'function') {
            return { html: '', nav: { mode: 'default', url: null } };
        }

        // ???????? renderCard ???????
        if (plugin && typeof plugin.renderCard === 'function') {
            return { html: plugin.renderCard(doc, helpers) || '', nav: navFromPlugin(plugin, doc, helpers) };
        }

        const defaultFragments = buildDefaultCardFragments(doc, ctx, helpers);
        const hasCardBlocks = !!(plugin && plugin.blocks &&
            Object.keys(plugin.blocks).some(k => k.startsWith('card/')));

        // ???????? -> ?????????????
        if (!hasCardBlocks) {
            return { html: null, nav: navFromPlugin(plugin, doc, helpers) };
        }

        // ???????? + ?????????
        const blockIds = [
            'card/title', 'card/meta/time', 'card/meta/vector', 'card/meta/source',
            'card/summary', 'card/debug/left', 'card/debug/right'
        ];
        const fragments = {};
        blockIds.forEach(id => {
            const override = resolve(plugin, id);
            fragments[id] = override ? String(override(doc, helpers)) : defaultFragments[id];
        });

        const uuid = ctx.escapeHTML(doc.UUID || 'Unknown-UUID');
        const html = composeCard(fragments);
        return { html: html || '', nav: navFromPlugin(plugin, doc, helpers) };
    }

    // ??????
    function navFromPlugin(plugin, doc, helpers) {
        if (!plugin) return { mode: 'default', url: null };
        if (typeof plugin.detailURL === 'function') {
            const url = plugin.detailURL(doc, helpers);
            return { mode: 'navigate', url: url && String(url).length ? url : null };
        }
        if (typeof plugin.onCardClick === 'function') {
            return { mode: 'custom', url: null };
        }
        return { mode: 'default', url: null };
    }

    // ------------------------------------------------------------------ ??
    function buildDetail(plugin, doc, ctx, helpers) {
        if (!ctx || typeof ctx.generateHTML !== 'function') {
            return { html: '' };
        }
        if (plugin && typeof plugin.renderDetail === 'function') {
            return { html: plugin.renderDetail(doc, helpers) || '' };
        }
        return { html: null };
    }

    function bindDetail(plugin, containerEl, doc, helpers) {
        if (plugin && typeof plugin.bindDetailEvents === 'function') {
            plugin.bindDetailEvents(containerEl, doc, helpers);
            return true;
        }
        return false;
    }

    // 平台下发给插件的只读工具集
    function makeHelpers(ctx, base, subsystem) {
        const helpers = {
            base: base || (window.IIS_BASE_PATH || ''),
            subsystem: subsystem || (window.IIS_SUBSYSTEM || ''),
        };
        if (ctx && typeof ctx.escapeHTML === 'function') helpers.escapeHTML = (s) => ctx.escapeHTML(s);
        if (ctx && typeof ctx.formatLocalTime === 'function') helpers.formatLocalTime = (s) => ctx.formatLocalTime(s);
        if (ctx && typeof ctx.isValidUrl === 'function') helpers.isValidUrl = (s) => ctx.isValidUrl(s);
        if (ctx && typeof ctx.createRatingStars === 'function') helpers.createRatingStars = (s) => ctx.createRatingStars(s);
        return helpers;
    }

    window.SubsystemUI = {
        register,
        getPlugin,
        hasPlugin,
        resolve,
        buildCard,
        buildDetail,
        bindDetail,
        navFromPlugin,
        makeHelpers,
    };
})();