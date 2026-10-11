/* static/js/article_detail_renderer.js */

window.ArticleDetailRenderer = {
    // 基础防 XSS 转义（正文需要渲染 HTML 时不使用这个）
    escapeHTML: function(str) {
        if (!str) return '';
        return String(str).replace(/[&<>"']/g, function(m) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
        });
    },

    anyTimeToTimeStr: function(s) {
        try {
            const d = new Date(s);
            if (isNaN(d.getTime())) return s || 'N/A';
            const y = d.getFullYear();
            const m = String(d.getMonth()+1).padStart(2,'0');
            const day = String(d.getDate()).padStart(2,'0');
            const hh = String(d.getHours()).padStart(2,'0');
            const mm = String(d.getMinutes()).padStart(2,'0');
            const ss = String(d.getSeconds()).padStart(2,'0');
            return `${y}-${m}-${day} ${hh}:${mm}:${ss}`;
        } catch { return s || 'N/A'; }
    },

    renderStars: function(score) {
        const num = Number(score);
        if (!Number.isFinite(num)) return '';
        const full = Math.floor(num / 2);
        const half = (num % 2) >= 1;
        const empty = 5 - full - (half ? 1 : 0);
        let html = '<span class="stars">';
        for (let i = 0; i < full; i++) html += '<i class="bi bi-star-fill text-warning"></i> ';
        if (half) html += '<i class="bi bi-star-half text-warning"></i> ';
        for (let i = 0; i < empty; i++) html += '<i class="bi bi-star text-warning"></i> ';
        html += ` <span class="ms-2 text-muted">${num}/10</span></span>`;
        return html;
    },

    renderRatingTable: function(article) {
        const rates = article?.analysis?.assessment?.rate || {};
        const manualRatings = article?.manual_rating || {};
        const keys = Object.keys(rates);
        if (!keys.length) return '';

        let rows = '';
        keys.forEach(key => {
            const score = rates[key];
            if (typeof score !== 'number' || score < 0 || score > 10) return;
            const manual = (manualRatings && manualRatings[key] != null) ? manualRatings[key] : score;
            rows += `
                <tr>
                    <td>${this.escapeHTML(key)}</td>
                    <td>${this.renderStars(score)}</td>
                    <td>
                        <input id="rating-${this.escapeHTML(key)}" type="number" class="form-control form-control-sm"
                            value="${manual}" min="0" max="10" step="0.5" style="width: 80px;"
                            oninput="if(this.value<0)this.value=0;if(this.value>10)this.value=10;">
                    </td>
                </tr>`;
        });

        return `
            <div class="mt-4">
                <h5><i class="bi bi-graph-up"></i> Analysis & Evaluation</h5>
                <div class="table-responsive">
                    <table class="table table-sm">
                        <thead><tr><th>Dimension</th><th>Rating</th><th>Manual Rating</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="mt-3">
                    <button id="submit-rating" class="btn btn-primary btn-sm">
                        <i class="bi bi-check-circle"></i> Submit Manual Ratings
                    </button>
                </div>
            </div>`;
    },

    humanLabel: function(value, labels = {}) {
        if (value == null || value === '') return '未说明';
        const text = String(value);
        return labels[text] || text.replace(/_/g, ' ');
    },

    eventEntities: function(article) {
        const result = {};
        const add = (key, entity) => {
            if (!key || !entity) return;
            result[String(key)] = entity;
        };
        (Array.isArray(article?.entities) ? article.entities : []).forEach(entity => {
            add(entity?._id || entity?.uuid || entity?.id, entity);
        });
        (article?.analysis?.event_extraction?.entities || []).forEach(entity => add(entity?.id, {
            canonical_name: entity?.name,
            entity_type: entity?.type,
            country_code: entity?.country_code
        }));
        return result;
    },

    normalizedEvents: function(article) {
        if (Array.isArray(article?.events) && article.events.length) return article.events;
        const extraction = article?.analysis?.event_extraction;
        if (!Array.isArray(extraction?.events)) return [];
        return extraction.events.map(event => {
            const core = event?.core || {};
            const roleBindings = [];
            Object.entries(core.roles || {}).forEach(([role, entityIds]) => {
                (entityIds || []).forEach(entityId => roleBindings.push({
                    role: role,
                    entity_uuid: entityId,
                    semantic_group: 'other'
                }));
            });
            return {
                _id: event.id,
                local_event_id: event.id,
                is_primary: event.id === extraction.primary_event_id,
                frame: core.frame,
                predicate: core.predicate,
                role_bindings: roleBindings,
                time: core.time || {},
                location_entity_uuids: core.context?.event_location || [],
                attributes: core.attributes || {},
                qualifiers: event.qualifiers || [],
                relations: (event.relations || []).map(relation => ({
                    predicate: relation.predicate,
                    target_event_uuid: relation.target_event_id,
                    surface: relation.surface
                }))
            };
        });
    },

    renderEntityChip: function(entityId, entityMap) {
        const entity = entityMap[String(entityId)] || {};
        const name = entity.canonical_name || entity.name || String(entityId || '未知实体');
        const extras = [entity.entity_type || entity.type, entity.country_code].filter(Boolean);
        const title = extras.length ? ` title="${this.escapeHTML(extras.join(' · '))}"` : '';
        return `<span class="event-entity-chip"${title}>${this.escapeHTML(name)}</span>`;
    },

    renderEventTime: function(time) {
        const labels = {
            event_time: '发生时间', start_time: '开始时间', end_time: '结束时间',
            effective_time: '生效时间', deadline: '截止时间',
            expected_start_time: '预计开始', expected_end_time: '预计结束'
        };
        return Object.entries(time || {}).map(([kind, value]) => {
            if (!value) return '';
            const raw = value.normalized || value.surface || value;
            const approximate = value.approximate ? '（约）' : '';
            const surface = value.surface && value.surface !== raw
                ? `<span class="event-origin-text">原文：${this.escapeHTML(value.surface)}</span>` : '';
            return `<span class="event-fact"><b>${this.escapeHTML(this.humanLabel(kind, labels))}：</b>${this.escapeHTML(raw)}${approximate}${surface}</span>`;
        }).filter(Boolean).join('');
    },

    renderEventAttributes: function(attributes) {
        const labels = {
            amount: '金额', quantity: '数量', ratio: '比例', value_before: '变化前',
            value_after: '变化后', delta: '变化量', duration: '持续时间', level: '等级/水平'
        };
        return Object.entries(attributes || {}).map(([name, attribute]) => {
            if (!attribute) return '';
            let value;
            if (attribute.type === 'money') {
                value = [attribute.currency, attribute.value, attribute.unit].filter(v => v != null && v !== '').join(' ');
            } else if (attribute.type === 'ratio' && Number.isFinite(Number(attribute.value))) {
                value = `${Number(attribute.value) * 100}%`;
            } else if (typeof attribute === 'object') {
                value = [attribute.value, attribute.unit, attribute.currency].filter(v => v != null && v !== '').join(' ');
            } else {
                value = String(attribute);
            }
            const surface = attribute?.surface && String(attribute.surface) !== String(value)
                ? `<span class="event-origin-text">原文：${this.escapeHTML(attribute.surface)}</span>` : '';
            return `<span class="event-fact"><b>${this.escapeHTML(this.humanLabel(name, labels))}：</b>${this.escapeHTML(value || '未说明')}${surface}</span>`;
        }).filter(Boolean).join('');
    },

    renderEventSection: function(article) {
        const events = this.normalizedEvents(article);
        if (!events.length) return '';

        const entityMap = this.eventEntities(article);
        const primaryUuid = String(article?.primary_event_uuid || '');
        const roleLabels = {
            actor: '行动方', target: '目标', subject: '主体', counterpart: '关联方',
            agent: '执行方', affected: '受影响方', authority: '主管方', person: '人员',
            organization: '组织', party: '参与方', participant: '参与方', belligerent: '交战方',
            source: '来源方', destination: '接收方', provider: '提供方', recipient: '接收方',
            theme: '转移对象', object: '对象', asset: '资产', instrument: '手段/工具',
            location: '地点', affected_area: '受影响地区', topic: '议题', position: '职位',
            holder: '持有方', controller: '控制方', controlled: '受控对象', member: '成员',
            dependent: '依赖方', dependency: '依赖对象', discoverer: '发现方', rule: '规则',
            goods: '物资/商品', sender: '发送方', information: '信息', inspector: '检查方',
            investigator: '调查方', agreement: '协议', electorate: '选民', case: '案件',
            releaser: '发布方', phenomenon: '现象', debtor: '债务人', obligation: '债务',
            buyer: '买方', seller: '卖方', acquirer: '收购方', investor: '投资方',
            resource: '资源', payer: '付款方', payee: '收款方', lender: '贷款方',
            borrower: '借款方', creditor: '债权人', issuer: '发行方', security: '证券',
            rater: '评级方', supplier: '供应方', operator: '运营方', producer: '生产方',
            product: '产品', builder: '建设方', developer: '开发方', tester: '测试方',
            deployer: '部署方', facility: '设施', capability: '能力'
        };
        const semanticLabels = {
            agent: '行动者', affected: '受作用者', participant: '参与者', theme: '主题对象',
            source: '来源端', destination: '去向端', instrument: '工具', authority: '主管主体',
            beneficiary: '受益方', location: '地点', other: '其他'
        };
        const qualifierTypeLabels = {
            phase: '阶段', intention: '意图', authorization: '授权', directive: '指令',
            epistemic: '信息状态', modality: '可能性', polarity: '极性'
        };
        const qualifierValueLabels = {
            asserted: '已陈述', estimated: '估计', doubted: '存疑', denied: '已否认',
            possible: '可能', probable: '很可能', conditional: '有条件', negated: '否定'
        };
        const relationLabels = {
            causes: '导致', promotes: '促进', prevents: '阻止', aggravates: '加剧',
            mitigates: '缓解', precedes: '先于', follows: '后于', overlaps: '时间重叠',
            condition_for: '是其条件', part_of: '属于'
        };
        const frameLabels = {
            state: '状态', process: '过程', change: '变化', intrinsic: '单体事件',
            relational: '关系事件', targeted: '指向目标', transfer: '转移事件',
            agentive: '有行动主体', non_agentive: '非行动主体', unknown: '主体未知'
        };
        const localIdByUuid = {};
        events.forEach(event => {
            localIdByUuid[String(event?._id || event?.uuid || event?.local_event_id || '')] = event?.local_event_id || event?._id;
        });

        const cards = events.map((event, index) => {
            const eventUuid = String(event?._id || event?.uuid || event?.local_event_id || '');
            const isPrimary = Boolean(event?.is_primary) || (primaryUuid && eventUuid === primaryUuid);
            const predicate = event?.predicate || {};
            const predicateTitle = predicate.surface || predicate.gloss || predicate.id || '未命名事件';
            const predicateId = predicate.id
                ? `<span class="event-code-badge">${this.escapeHTML(predicate.id)}</span>` : '';

            const roles = (event?.role_bindings || []).map(binding => {
                const role = this.humanLabel(binding.role, roleLabels);
                const semantic = binding.semantic_group && binding.semantic_group !== 'other'
                    ? `<small>${this.escapeHTML(this.humanLabel(binding.semantic_group, semanticLabels))}</small>` : '';
                return `<div class="event-role"><span class="event-role-label">${this.escapeHTML(role)}</span>${this.renderEntityChip(binding.entity_uuid, entityMap)}${semantic}</div>`;
            }).join('');

            const times = this.renderEventTime(event?.time);
            const locations = (event?.location_entity_uuids || [])
                .map(value => this.renderEntityChip(value, entityMap)).join('');
            const attributes = this.renderEventAttributes(event?.attributes);
            const qualifiers = (event?.qualifiers || []).map(qualifier => {
                const by = (qualifier.by || []).map(value => this.renderEntityChip(value, entityMap)).join('');
                const qualifierTime = qualifier.time
                    ? ` · ${this.escapeHTML(qualifier.time.normalized || qualifier.time.surface || '')}` : '';
                const surface = qualifier.surface
                    ? `<span class="event-origin-text">原文：${this.escapeHTML(qualifier.surface)}</span>` : '';
                return `<div class="event-qualifier"><b>${this.escapeHTML(this.humanLabel(qualifier.type, qualifierTypeLabels))}：</b>${this.escapeHTML(this.humanLabel(qualifier.value, qualifierValueLabels))}${qualifierTime}${by ? `<span class="event-by">声明方：${by}</span>` : ''}${surface}</div>`;
            }).join('');
            const relations = (event?.relations || []).map(relation => {
                const target = localIdByUuid[String(relation.target_event_uuid)] || relation.target_event_uuid || '未知事件';
                const surface = relation.surface ? `（${this.escapeHTML(relation.surface)}）` : '';
                return `<span class="event-relation">${this.escapeHTML(this.humanLabel(relation.predicate, relationLabels))} <b>${this.escapeHTML(target)}</b>${surface}</span>`;
            }).join('');
            const frame = event?.frame || {};
            const frameBadges = ['dynamics', 'topology', 'agency'].map(key => frame[key]
                ? `<span class="event-frame-badge">${this.escapeHTML(this.humanLabel(frame[key], frameLabels))}</span>` : '').join('');
            const observedAt = event?.observed_at
                ? `<span><b>系统观察时间：</b>${this.escapeHTML(this.anyTimeToTimeStr(event.observed_at))}</span>` : '';

            return `
                <article class="event-card${isPrimary ? ' primary' : ''}">
                    <header class="event-card-header">
                        <div>
                            <span class="event-sequence">事件 ${index + 1}</span>
                            ${isPrimary ? '<span class="event-primary-badge">主事件</span>' : ''}
                            <h4>${this.escapeHTML(predicateTitle)} ${predicateId}</h4>
                        </div>
                    </header>
                    ${roles ? `<div class="event-block"><div class="event-block-title">参与者与角色</div><div class="event-roles">${roles}</div></div>` : ''}
                    ${times ? `<div class="event-block"><div class="event-block-title">时间</div><div class="event-facts">${times}</div></div>` : ''}
                    ${locations ? `<div class="event-block"><div class="event-block-title">地点</div><div class="event-chips">${locations}</div></div>` : ''}
                    ${attributes ? `<div class="event-block"><div class="event-block-title">数值与属性</div><div class="event-facts">${attributes}</div></div>` : ''}
                    ${qualifiers ? `<div class="event-block"><div class="event-block-title">状态与限定</div>${qualifiers}</div>` : ''}
                    ${relations ? `<div class="event-block"><div class="event-block-title">与其他事件的关系</div><div class="event-relations">${relations}</div></div>` : ''}
                    <details class="event-technical">
                        <summary>查看结构信息</summary>
                        <div>${frameBadges}</div>
                        <div class="event-technical-text">
                            <span><b>事件编号：</b>${this.escapeHTML(event.local_event_id || `E${index + 1}`)}</span>
                            ${eventUuid ? `<span><b>事件 UUID：</b>${this.escapeHTML(eventUuid)}</span>` : ''}
                            ${observedAt}
                        </div>
                    </details>
                </article>`;
        }).join('');

        return `
            <section class="event-section" aria-labelledby="v4-event-heading">
                <div class="event-section-heading">
                    <div>
                        <h3 id="v4-event-heading"><i class="bi bi-diagram-3"></i> 事件结构</h3>
                        <p>AI 从文章中抽取的结构化事件描述，可用于理解参与者、时间、地点及事件之间的关系。</p>
                    </div>
                    <span class="event-count">${events.length} 个事件</span>
                </div>
                <div class="event-notice"><i class="bi bi-info-circle"></i> 以下内容是对来源报道的结构化整理，不代表系统已独立核实相关事实。</div>
                <div class="event-list">${cards}</div>
            </section>`;
    },

    // 统一生成 HTML 结构
    generateHTML: function(article) {
        // 先问插件：有整详情则由协议处理，否则回退默认实现
        const plugin = (window.SubsystemUI && window.SubsystemUI.getPlugin(window.IIS_SUBSYSTEM || '')) || null;
        if (plugin) {
            const helpers = window.SubsystemUI.makeHelpers
                ? window.SubsystemUI.makeHelpers(this, window.IIS_BASE_PATH || '', window.IIS_SUBSYSTEM || '')
                : { base: window.IIS_BASE_PATH || '', subsystem: window.IIS_SUBSYSTEM || '' };
            const built = window.SubsystemUI.buildDetail(plugin, article, this, helpers);
            if (built && built.html) return built.html;
        }

        const uuid = this.escapeHTML(article?.intelligence_uuid || article?._id || '');
        const informant = article?.informant ? this.escapeHTML(article.informant) : '';
        const contentTime = this.anyTimeToTimeStr(
            article?.content_time || article?.archived_at || 'N/A');
        const contentTimeSource = article?.content_time_source
            || window.IntelligenceContract.ContentTimeSource.ARCHIVED;
        const contentTimeLabel = window.IntelligenceContract.contentTimeLabel(
            contentTimeSource, 'zh');
        const title = this.escapeHTML(article?.analysis?.message?.title || 'No Title');
        const brief = this.escapeHTML(article?.analysis?.message?.brief || 'No Brief');

        // 正文保持原文渲染，不做强制转义以支持排版。若需纯文本展示请加上 this.escapeHTML()
        const content = this.escapeHTML(article?.analysis?.message?.text || 'No Content');
        const entities = Array.isArray(article?.entities) ? article.entities : [];
        const namesOf = types => entities.filter(item => types.includes(item.entity_type))
            .map(item => this.escapeHTML(item.canonical_name));
        const locations = namesOf(['location', 'country', 'region', 'city', 'facility']);
        const people = namesOf(['person']);
        const orgs = namesOf(['organization', 'agency', 'company']);
        const times = (article?.events || []).flatMap(event => Object.values(event.time || {}))
            .map(value => this.anyTimeToTimeStr(value.normalized || value.surface || value));
        const impact = this.escapeHTML(article?.analysis?.assessment?.impact || 'No Impact');
        const tips = this.escapeHTML(article?.analysis?.assessment?.tips || 'No Tips');

        // [修复] 补全了 <a> 标签
        return `
        <div class="article-preview" id="article-root-${uuid}" data-uuid="${uuid}">
            <section class="header-box key-points">
                <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between;">
                    <div style="min-width:0;">
                        <div style="font-size:12px; color:#555;">
                            <i class="bi bi-calendar-event"></i> ${contentTimeLabel}：${contentTime}
                            &nbsp;|&nbsp; <i class="bi bi-upc-scan"></i> ${uuid}
                        </div>
                        <h3 style="margin:6px 0 0; font-size:20px; line-height:1.35;">${title}</h3>
                        <div style="margin-top:6px; color:#333;">${brief}</div>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center;">
                        ${informant ? `<a href="${informant}" target="_blank" class="btn btn-outline-secondary btn-sm"><i class="bi bi-link-45deg"></i> Source</a>` : ''}
                        ${window.IIS_VECTOR_ENABLED === false ? '' : `<a href="${window.IIS_BASE_PATH || ''}/intelligences?search_mode=vector_similar&reference=${uuid}&score_threshold=0.6" class="btn btn-outline-primary btn-sm">
                            <i class="bi bi-intersect"></i> Find Similar
                        </a>`}
                    </div>
                </div>
            </section>

            <section class="meta-grid">
                <div class="meta-card">
                    <h5><i class="bi bi-geo-alt"></i> Geographic Locations</h5>
                    ${locations.length ? locations.join(', ') : 'No location data'}
                </div>
                <div class="meta-card">
                    <h5><i class="bi bi-people"></i> Related People</h5>
                    ${people.length ? people.join(', ') : 'No associated people'}
                </div>
                <div class="meta-card">
                    <h5><i class="bi bi-building"></i> Related Organizations</h5>
                    ${orgs.length ? orgs.join(', ') : 'No related organizations'}
                </div>
            </section>

            <section class="meta-card" style="margin-top:10px;">
                <h5><i class="bi bi-clock-history"></i> Event Time(s)</h5>
                ${times.length ? times.join(', ') : 'No specific timing data'}
            </section>

            <section class="content-section" style="margin-top:16px;">
                ${content}
            </section>

            ${this.renderEventSection(article)}

            <section style="margin-top:16px;">
                ${this.renderRatingTable(article)}
            </section>

            <section class="impact-card" style="margin-top:16px;">
                <h5><i class="bi bi-lightning-charge"></i> Potential Impact</h5>
                <p>${impact}</p>
            </section>

            <section class="tip-card" style="margin-top:12px;">
                <h5><i class="bi bi-lightbulb"></i> Analyst Notes</h5>
                <p>${tips}</p>
            </section>
        </div>`;
    },

    // 绑定评分提交逻辑
    bindEvents: function(containerElement, uuid, toastContainerId) {
        const submitBtn = containerElement.querySelector('#submit-rating');
        if (!submitBtn) return;

        submitBtn.addEventListener('click', async () => {
            const ratings = {};
            containerElement.querySelectorAll('input[id^="rating-"]').forEach(input => {
                const dim = input.id.replace('rating-', '');
                ratings[dim] = input.value;
            });

            try {
                const r = await fetch((window.IIS_BASE_PATH || '') + '/manual_rate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        uuid: uuid,
                        ratings: ratings,
                        subsystem: window.IIS_SUBSYSTEM || '',
                        timestamp: new Date().toISOString()
                    })
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                this.showToast(toastContainerId, 'Ratings submitted successfully!', 'success');
            } catch (e) {
                this.showToast(toastContainerId, 'Error submitting ratings: ' + (e.message || e), 'danger');
            }
        });
    },

    showToast: function(containerId, message, type) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `article-toast ${type}`;
        toast.innerHTML = `<div style="flex:1;">${message}</div><button class="toast-close"><i class="bi bi-x-lg"></i></button>`;
        container.appendChild(toast);
        const remove = () => toast.remove();
        toast.querySelector('.toast-close').addEventListener('click', remove);
        setTimeout(remove, 2400);
    }
};
