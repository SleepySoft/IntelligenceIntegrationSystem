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
        // 配色类插件：提供 style 字符串即注入 <style> 标签，不改变卡片结构与行为
        if (plugin.style && typeof plugin.style === 'string') {
            let styleTag = document && document.getElementById('subsystem-ui-style-' + plugin.name);
            if (!styleTag) {
                styleTag = document.createElement('style');
                styleTag.id = 'subsystem-ui-style-' + plugin.name;
                (document.head || document.body).appendChild(styleTag);
            }
            styleTag.textContent = plugin.style;
        }
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
        const documentId = doc.intelligence_uuid || doc._id || 'Unknown-UUID';
        const uuid = ctx.escapeHTML(documentId);
        const base = helpers.base || '';
        const intelUrl = base + '/intelligence/' + encodeURIComponent(documentId);
        const analysis = doc.analysis || {};
        const message = analysis.message || {};
        const classification = analysis.classification || {};
        const rawData = doc.raw_data || {};

        // ---- meta/time ----
        const raw_archived = doc.archived_at || '';
        let archived_html = '';
        if (raw_archived) {
            archived_html = `<span class="article-time archived-time" data-archived="${ctx.escapeHTML(raw_archived)}">Archived: ${ctx.formatLocalTime(raw_archived)}</span>`;
        }
        const pub_time_raw = rawData.pub_time || rawData.collect_time;
        const time_block = `                ${archived_html}\n                <span class="article-time">Publish: ${ctx.formatLocalTime(pub_time_raw)}</span>`;

        // ---- meta/vector ----
        const vector_score = doc.vector_score;
        let vector_block = '                ';
        if (vector_score !== undefined && vector_score !== null) {
            const formattedScore = parseFloat(vector_score).toFixed(3);
            let badgeClass = vector_score >= 0.8 ? 'bg-success' :
                            (vector_score >= 0.6 ? 'bg-primary' :
                            (vector_score >= 0.4 ? 'bg-warning' : 'bg-danger'));
            vector_block = `                <span class="badge ${badgeClass} similarity-badge"><span class="similarity-score">${formattedScore}</span></span>`;
        }

        // ---- meta/source ----
        const informant_val = doc.informant || rawData.informant || rawData.source || '';
        const informant = ctx.escapeHTML(informant_val);
        const informant_html = ctx.isValidUrl(informant)
            ? `<a href="${informant}" target="_blank" class="source-link">${informant}</a>`
            : (informant || 'Unknown Source');
        const source_block = `                <span class="article-source">Source: ${informant_html}</span>`;

        // ---- title ----
        const title_text = ctx.escapeHTML(message.title || 'No Title');
        const title_block = `              <a href="${intelUrl}" class="article-title" data-uuid="${uuid}">\n                ${title_text}\n              </a>`;

        // ---- summary ----
        const summary_block = `            <p class="article-summary">${ctx.escapeHTML(message.brief || 'No Brief')}</p>`;

        // ---- debug/left ----
        const prompt_version = doc.prompt_version;
        const taxonomy = ctx.escapeHTML(classification.taxonomy || 'Unclassified');
        const sub_categories = classification.subcategories || [];
        const total_score = doc.total_score;
        const tags_html = Array.isArray(sub_categories)
            ? sub_categories.map(tag => `<span class="category-tag">${ctx.escapeHTML(tag)}</span>`).join('')
            : '';
        const category_line = `<div style="margin-bottom: 4px; display: flex; align-items: center; gap: 6px;">\n                <span class="debug-label" style="color:#1a73e8; font-size:0.95rem;">${taxonomy}</span>\n                ${tags_html}\n            </div>`;
        const total_score_html = total_score !== undefined && total_score !== null
            ? `\n                <div class="article-rating" style="margin: 6px 0 4px 0;">\n                    <span class="debug-label">\u603b\u5206:</span>\n                    ${ctx.createRatingStars(total_score)}\n                </div>`
            : '';
        const left_content = `\n            ${category_line}\n            ${total_score_html}\n            <div>\n                <span class="debug-label">UUID:</span> ${uuid}\n            </div>`;
        const debug_left_block = left_content;

        // ---- debug/right ----
        let right_content = '';
        const ai_service = ctx.escapeHTML(doc.ai_service || '');
        const ai_model = ctx.escapeHTML(doc.ai_model || '');
        if (ai_service || ai_model) {
            if (ai_service) right_content += `<div><span class="debug-label">Service:</span><span class="debug-value-truncate" title="${ai_service}">${ai_service}</span></div>`;
            if (ai_model) right_content += `<div><span class="debug-label">Model:</span><span class="debug-value-truncate" title="${ai_model}">${ai_model}</span></div>`;
        }
        if (prompt_version) {
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
