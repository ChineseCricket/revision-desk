import {marked} from './marked.js'

const $ = id => document.getElementById(id)
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
const names = {pending:'待审阅',accepted:'已接受',rejected:'已拒绝',needs_revision:'要求修改',needs_info:'待补信息',not_started:'未落实',applied:'已登记落实',verified:'已登记核验',open:'重新打开',closed:'验收关闭'}
const actionNames = {decision:'审批',comment:'评论',implementation:'落实记录',closure:'验收'}
let data, state, token, selected, group = 'all', grouping = 'task', page = 1, mode = 'pdf', tab = 'proposal', busy = false
let documentVersion = 'original'
const readerPositions = {}
let zoom = 100
let focusedEdit = null
let scrollRequest = 0
let batchItems = []
let checkingFreshness = false
let draftKey, drafts = {}

function toast(message) {
    $('toast').textContent = message
    $('toast').hidden = false
    clearTimeout(toast.timer)
    toast.timer = setTimeout(() => $('toast').hidden = true, 5000)
}

function markdown(text) {
    // 方案可包含TeX/HTML字符，渲染后仅保留阅读所需标签和安全链接。
    const template = document.createElement('template')
    template.innerHTML = marked.parse(text)
    const allowed = new Set(['P','BR','STRONG','EM','CODE','PRE','BLOCKQUOTE','H1','H2','H3','H4','UL','OL','LI','TABLE','THEAD','TBODY','TR','TH','TD','HR','A','DEL'])
    for (const element of [...template.content.querySelectorAll('*')]) {
        if (!allowed.has(element.tagName)) {
            element.replaceWith(document.createTextNode(element.textContent))
            continue
        }
        const href = element.getAttribute('href')
        for (const attr of [...element.attributes]) element.removeAttribute(attr.name)
        if (element.tagName === 'A' && /^https?:\/\//i.test(href || '')) {
            element.setAttribute('href', href)
            element.setAttribute('target', '_blank')
            element.setAttribute('rel', 'noopener noreferrer')
        }
    }
    return template.innerHTML
}

function filtered() {
    const query = $('search').value.toLowerCase().trim()
    const status = $('status').value
    return data.items.filter(item => (status === 'withdrawn' ? item.lifecycle === 'withdrawn' : item.lifecycle !== 'withdrawn') && (!item.review_deferred || group === item.task || status === 'reply') && (group === 'all' || item.task === group || item.issues.includes(group)) &&
        (!query || `${item.id} ${item.title} ${item.body} ${item.original}`.toLowerCase().includes(query)) &&
        (status === 'next_round' ? needsReview(item) : ['all','withdrawn'].includes(status) || (['reply','change'].includes(status) ? item.kind === status : status === 'unlocated' ? !itemAnchors(item).length : state.items[item.id].decision === status)))
}

function needsReview(item) {
    return item.lifecycle !== 'withdrawn' && !item.review_deferred && item.review_stage !== 'addressed' && !['accepted','rejected'].includes(state.items[item.id].decision)
}

function itemStatus(item) {
    if (state.items[item.id].chat_authorized) return '已按聊天授权落实'
    return item.review_deferred ? '暂缓审阅' : item.review_stage === 'addressed' ? '已按评论处理' : names[state.items[item.id].decision]
}

function renderGroups() {
    const entities = grouping === 'task' ? data.tasks : data.issues
    $('byTask').classList.toggle('active', grouping === 'task')
    $('byIssue').classList.toggle('active', grouping === 'issue')
    const activeItems = data.items.filter(i=>i.lifecycle!=='withdrawn' && !i.review_deferred)
    $('groups').innerHTML = `<button class="group ${group === 'all' ? 'active' : ''}" data-group="all"><strong>当前返修</strong><span>${activeItems.length} 项 · ${data.tasks.length} 项任务 · ${data.issues.length} 条意见</span><span>旧提案可在状态筛选中查看</span></button>` + entities.map(g => {
        const s = state.groups[g.id]
        const title = grouping === 'task' ? g.title.replace(/修改详细方案|详细方案| v\d+/g,'') : g.comment
        return `<button class="group ${group === g.id ? 'active' : ''}" data-group="${g.id}"><strong>${g.id}　${g.review_deferred ? '暂缓审阅' : s.closed ? '✓ 已验收' : s.resolved + '/' + s.total + ' 已处理'}</strong><span>${escape(title.length > 45 ? title.slice(0,45) + '…' : title)}</span><div class="bar"><i style="width:${100*s.resolved/Math.max(1,s.total)}%"></i></div></button>`
    }).join('')
    $('groups').querySelectorAll('[data-group]').forEach(b => b.onclick = () => {
        group = b.dataset.group
        applyFilter()
    })
    const verified = activeItems.filter(i => state.items[i.id].implementation === 'verified').length
    $('summary').textContent = `${activeItems.filter(needsReview).length} 项待审阅 · ${verified} 项已核验`

}

function applyFilter() {
    const list = filtered()
    if (list.length) {
        select(list.some(i => i.id === selected) ? selected : list[0].id)
    } else {
        renderNavigation()
        $('detail').hidden = true
        $('empty').hidden = false
        $('empty').textContent = '当前筛选下没有修改项，请调整分类或筛选条件。'
    }
}

function renderNavigation() {
    renderGroups()
    const entity = [...data.tasks, ...data.issues].find(x => x.id === group)
    const pending = data.items.filter(needsReview)
    const inputCount = pending.filter(i => i.kind === 'question').length
    const replies = data.items.filter(i=>i.kind==='reply' && i.lifecycle!=='withdrawn')
    const deferredReplies = replies.filter(i=>i.review_deferred).length
    const pendingReplies = replies.filter(needsReview).length
    const replyProgress = deferredReplies ? `${deferredReplies}条回复暂缓审阅` : `${pendingReplies}条回复待审阅`
    $('groupTitle').textContent = group === 'all' ? '原文对照 · 全部修改' : `${group} · ${entity.title || '审稿意见'}`
    $('groupProgress').textContent = group === 'all' ? `${pending.length-inputCount}组待审候选 · ${inputCount}项待确认 · ${replyProgress}；已处理项可切换筛选查看` : `${state.groups[group].resolved}/${state.groups[group].total} 项已处理 · ${state.groups[group].verified} 项已拒绝或核验 · ${state.groups[group].closed ? '作者已验收' : '尚未验收'}${state.groups[group].technical_pending?.length ? ' · '+state.groups[group].technical_pending.length+'项技术待核验' : ''}`
    const technical = group === 'all' ? [] : (state.groups[group].technical_pending || [])
    $('groupDescription').hidden = !entity?.comment && !technical.length
    $('groupDescription').textContent = [entity?.comment || '', ...technical.map(t=>'技术待核验：'+t.title+'。'+t.body)].filter(Boolean).join('\n\n')
    $('closeGroup').hidden = group === 'all'
    $('closeGroup').textContent = group !== 'all' && state.groups[group].closed ? '重新打开此组' : '验收此组'
    const list = filtered()
    $('queueTitle').textContent = `当前 ${list.length} 项提案`
    $('queue').innerHTML = list.length ? list.map(i => `<button class="queue-item ${i.id === selected ? 'active' : ''}" data-item="${i.id}"><i class="dot ${state.items[i.id].decision}"></i><div>${escape(i.task + ' · ' + i.title)}<small>${i.lifecycle==='withdrawn'?'已撤回 · '+names[state.items[i.id].decision]:itemStatus(i)} · ${itemAnchors(i).length ? (documentVersion === 'current' ? '当前稿第' : '原件第')+[...new Set(itemAnchors(i).map(a=>a.page))].join('/')+'页' : itemLines(i).length ? '源行 '+itemLines(i).join('/')+' · PDF待定位' : '全局/参考资料提案'}${i.kind === 'reply' ? ' · 回复候选' : ''}</small></div></button>`).join('') : '<p style="padding:16px;color:#819086">当前筛选下没有提案。</p>'
    $('queue').querySelectorAll('[data-item]').forEach(b => b.onclick = () => select(b.dataset.item))
    renderPaper()
}

function readerDocument() {
    return documentVersion === 'current' ? (data.current_document || {pages:[], source_lines:[], label:'当前稿件', source_label:'当前源码尚未载入'}) : {pages:data.pages, source_lines:data.source_lines, label:'送审原稿', source_label:data.source_label}
}

function itemAnchors(item) {
    return documentVersion === 'current' ? (data.current_locations?.[item.id]?.anchors || item.current_anchors || []) : item.anchors
}

function itemLines(item) {
    return documentVersion === 'current' ? (data.current_locations?.[item.id]?.lines || item.current_lines || []) : item.lines
}

function editLocation(item, index) {
    const edit = item.edits[index]
    const current = data.current_locations?.[item.id]?.edits?.[index]
    const anchor = documentVersion === 'current' ? (current ? current.anchor : edit.current_anchor) : edit.anchor
    const line = documentVersion === 'current' ? (current ? current.line : edit.current_line) : edit.line
    if (documentVersion === 'current' && current) return {line:Number.isInteger(line) ? line : null, anchor}
    if (anchor) return {line:Number.isInteger(line) ? line : null, anchor}
    if (Number.isInteger(line)) {
        const own = itemAnchors(item).filter(a => a.line === line)
        const anchors = own.length ? own : data.items.flatMap(i => itemAnchors(i).filter(a => a.line === line))
        return {line, anchor:anchors.reduce((best, a) => !best || a.score > best.score ? a : best, null)}
    }
    return {line:null, anchor:documentVersion === 'original' && line === '文献条目' && item.anchors.length === 1 ? item.anchors[0] : null}
}

function switchDocument(version) {
    readerPositions[documentVersion] = {page, mode, top:$('paperScroll').scrollTop, left:$('paperScroll').scrollLeft}
    documentVersion = version
    const previous = readerPositions[version]
    page = previous?.page || 1
    mode = previous?.mode || 'pdf'
    const focus = focusedEdit
    focusedEdit = null
    if (focus?.item === selected) {
        renderNavigation()
        locateEdit(focus.index)
    }
    else if (selected) select(selected, true)
    else renderPaper()
    // 无选中修改时恢复各版本各自的阅读位置。
    if (!selected && previous) {
        $('paperScroll').scrollTop = previous.top
        $('paperScroll').scrollLeft = previous.left
    }
}

function updateEditFocus() {
    $('detailContent').querySelectorAll('[data-edit]').forEach(box => {
        const active = focusedEdit?.item === selected && focusedEdit.index === Number(box.dataset.edit)
        box.classList.toggle('located', active)
        box.setAttribute('aria-pressed', String(active))
    })
}

function scrollToOriginal(target) {
    const request = ++scrollRequest
    if (!target) return
    const position = () => requestAnimationFrame(() => {
        if (request !== scrollRequest || !target.isConnected) return
        const scroller = $('paperScroll')
        const rect = target.getBoundingClientRect()
        const viewport = scroller.getBoundingClientRect()
        scroller.scrollTop += rect.top - viewport.top - Math.max(24, (scroller.clientHeight - rect.height) / 2)
        if (rect.left < viewport.left || rect.right > viewport.right) {
            scroller.scrollLeft += rect.left - viewport.left - Math.max(18, (scroller.clientWidth - rect.width) / 2)
        }
    })
    const image = $('paper').querySelector('img')
    if (image && !image.complete) image.addEventListener('load', position, {once:true})
    else position()
}

function locateEdit(index) {
    const item = data.items.find(i => i.id === selected)
    const location = editLocation(item, index)
    focusedEdit = {item:selected, index, ...location}
    mode = location.anchor ? 'pdf' : 'source'
    if (location.anchor) page = location.anchor.page
    // 保留右侧滚动位置、讨论内容与草稿，仅更新原文及当前框的高亮。
    renderPaper()
    updateEditFocus()
    scrollToOriginal($('paper').querySelector('.edit-target'))
}

function renderPaper() {
    scrollRequest++
    const documentData = readerDocument()
    const pages = documentData.pages || []
    if (!pages.length && mode === 'pdf') mode = 'source'
    page = Math.max(1, Math.min(page, pages.length || 1))
    $('page').innerHTML = pages.map(p=>`<option value="${p.number}">第 ${p.number} 页</option>`).join('')
    $('page').value = page
    $('page').disabled = !pages.length
    $('prevPage').disabled = !pages.length || page === 1
    $('nextPage').disabled = !pages.length || page === pages.length
    $('mode').textContent = mode === 'pdf' ? '查看源文' : '返回PDF'
    $('mode').disabled = !pages.length
    $('documentVersion').value = documentVersion
    $('readerNotice').textContent = mode === 'pdf' ? `${documentData.label} · 黄色框为此版本段落定位，数字为关联提案数。右侧对照框保留提案原文及候选。` : documentData.source_label
    const list = filtered()
    $('paper').style.width = zoom + '%'
    $('paper').style.maxWidth = zoom === 100 ? '950px' : 'none'
    $('zoom').textContent = zoom + '%'
    const focus = focusedEdit?.item === selected ? focusedEdit : null
    if (focus) {
        $('readerNotice').textContent = mode === 'pdf'
            ? `当前修改 ${focus.index+1} · ${documentData.label}第 ${page} 页 · 蓝框标示对应段落或文献编号。`
            : focus.line ? `当前修改 ${focus.index+1} · ${documentVersion === 'current' ? '当前' : '审批前'}源文第 ${focus.line} 行${focus.anchor ? '。' : ' · 此处尚无PDF定位，已转到对应源文。'}`
            : `当前修改 ${focus.index+1} · ${documentVersion === 'current' ? '当前源码尚无可靠行号' : '文献原始条目'}${focus.anchor ? '。' : ' · 此条目尚无PDF定位。'}`
    }
    if (mode === 'source') {
        const current = data.items.find(i => i.id === selected)
        const target = current ? itemLines(current) : []
        if (documentVersion === 'current' && (!pages.length || (focus && !focus.anchor))) {
            const locationNote = focus?.line ? '第 '+focus.line+' 行' : focus ? '；此修改尚无可靠行号，请在源码中核对' : '；点击右侧对照框可定位修改行'
            $('readerNotice').textContent = `${documentData.label || '当前稿件'} · ${!pages.length ? '当前PDF尚未载入' : '此处尚无当前PDF定位'}，显示当前源码${locationNote}。${documentData.notice || ''}`
        }
        if (focus && !focus.line && documentVersion === 'original') {
            $('paper').innerHTML = `<div class="source-row edit-target"><b>修改前文献条目</b>\n${escape(current.edits[focus.index].before || current.original)}</div>`
            return
        }
        if (documentVersion === 'current' && focus && !focus.line && current.edits[focus.index].current_original) {
            $('paper').innerHTML = `<div class="source-row edit-target"><b>当前条目</b>\n${escape(current.edits[focus.index].current_original)}</div>`
            return
        }
        $('paper').innerHTML = (documentData.source_lines || []).map((line,index) => {
            if ((!line.trim()) && focus?.line !== index+1) return ''
            const matches = list.filter(i => itemLines(i).includes(index+1))
            return `<div class="source-row ${target.includes(index+1) ? 'target' : ''} ${focus?.line === index+1 ? 'edit-target' : ''}" id="line-${index+1}"><b>${index+1}</b>${escape(line)}${matches.map(i=>`<button data-item="${i.id}">${escape(i.task+' · '+i.title)}</button>`).join('')}</div>`
        }).join('') || '<p class="source-row">当前源码尚未载入。请先更新审阅材料；未使用送审原稿替代。</p>'
        $('paper').querySelectorAll('[data-item]').forEach(b => b.onclick = () => select(b.dataset.item))
        return
    }
    const p = pages[page-1]
    const clusters = new Map()
    for (const i of list) for (const a of itemAnchors(i).filter(a => a.page === page)) {
        const key = a.rect.map(x=>Math.round(x)).join(',')
        if (!clusters.has(key)) clusters.set(key,{a,items:[]})
        clusters.get(key).items.push(i)
    }
    $('paper').innerHTML = `<img src="${p.image}" alt="${escape(documentData.label)}第${page}页">` + [...clusters.values()].map(({a,items},idx)=>{
        const [x,y,x1,y1] = a.rect
        const active = items.some(i=>i.id===selected)
        const accepted = items.every(i=>state.items[i.id].decision==='accepted')
        return `<button class="pin ${active?'selected':''} ${accepted?'accepted':''}" data-cluster="${idx}" style="left:${100*x/p.width}%;top:${100*y/p.height}%;width:${100*(x1-x)/p.width}%;height:${100*(y1-y)/p.height}%" aria-label="${escape(items.map(i=>i.task+' '+i.title).join('；'))}"><span>${items.length} 项</span></button>`
    }).join('')
    if (focus?.anchor?.page === page) {
        const [x,y,x1,y1] = focus.anchor.rect
        $('paper').insertAdjacentHTML('beforeend', `<div class="edit-target pdf-edit-target" style="left:${100*x/p.width}%;top:${100*y/p.height}%;width:${100*(x1-x)/p.width}%;height:${100*(y1-y)/p.height}%" aria-label="当前修改 ${focus.index+1} 的原文位置"><span>修改 ${focus.index+1}</span></div>`)
    }
    const values = [...clusters.values()]
    $('paper').querySelectorAll('[data-cluster]').forEach(b=>b.onclick=()=>{
        const candidates = values[Number(b.dataset.cluster)].items
        const next = candidates.find(i=>i.id!==selected && state.items[i.id].decision==='pending') || candidates[0]
        select(next.id, false)
    })
}

function select(id, navigate = true) {
    if (selected) drafts[selected] = $('comment').value
    if (selected !== id || navigate) focusedEdit = null
    selected = id
    const i = data.items.find(x=>x.id===id)
    if (!i) return
    history.replaceState(null,'','#'+id)
    $('comment').value = drafts[id] || ''
    if (navigate) {
        const anchors = itemAnchors(i)
        if (anchors.length && readerDocument().pages?.length) page = anchors[0].page
        else mode = 'source'
    }
    renderNavigation()
    renderDetail()
    if (navigate) scrollToOriginal(mode === 'source' ? $('line-'+itemLines(i)[0]) : $('paper').querySelector('.pin.selected'))
}

function renderDetail() {
    const i = data.items.find(x=>x.id===selected)
    if (!i) return
    const s = state.items[i.id]
    const evidence = data.current_evidence?.[i.id] || i
    $('empty').hidden = true
    $('detail').hidden = false
    $('itemId').textContent = i.id + ' · v' + i.version.slice(0,6)
    $('itemTitle').textContent = i.title
    $('tags').innerHTML = [i.task,...i.issues].map(t=>`<span class="badge">${t}</span>`).join('')
    const withdrawn = i.lifecycle === 'withdrawn'
    const implementationLabel = i.preview_applied && s.decision !== 'accepted' ? '已试排 · 待复审' : names[s.implementation]
    $('itemState').textContent = withdrawn ? '旧提案已撤回 / 合并 · 历史保留' : `${itemStatus(i)} · ${implementationLabel} · ${i.mapping}`
    for (const id of ['accept','reject','reopen','needsRevision','needsInfo','applied','verified']) $(id).disabled=withdrawn || i.review_deferred
    $('itemState').className = 'status-label'
    $('historyCount').textContent = s.history.length ? '('+s.history.length+')' : ''
    document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab))
    if (tab === 'history') {
        $('detailContent').innerHTML = `<p>当前提案版本 ${i.version}。评论及审批只追加，不覆盖旧记录。</p>` + (s.history.length ? s.history.map(e=>`<article class="event ${e.version!==i.version?'old':''}"><strong>${escape(e.actor)} · ${actionNames[e.action]} ${escape(names[e.value]||'')}</strong><small>${new Date(e.time).toLocaleString('zh-CN')} · #${e.seq}${e.version!==i.version?' · 旧版记录，未继承批准':''}</small><p>${escape(e.text || '未附说明')}</p>${e.version!==i.version && state.proposals?.[i.id+':'+e.version] ? '<details><summary>查看当时的提案</summary>'+markdown(state.proposals[i.id+':'+e.version].body) + (state.proposals[i.id+':'+e.version].edits || []).map(edit=>'<pre>'+escape(edit.before)+' → '+escape(edit.after)+'</pre>').join('')+'</details>':''}</article>`).join('') : '<p>尚无讨论。历史方案的技术审查不代表您的审批。</p>')
        const versions = Object.values(state.proposals).filter(p => p.id === i.id)
        $('detailContent').innerHTML += '<h3>全部提案版本</h3>' + versions.map(p =>
            '<details><summary>' + escape(p.title) + ' · ' + escape(p.version.slice(0, 6)) +
            (p.version === i.version ? ' · 当前版本' : '') + '</summary>' + markdown(p.body) +
            (p.edits || []).map(edit => '<pre>' + escape(edit.before) + ' → ' + escape(edit.after) + '</pre>').join('') +
            '</details>').join('')
    } else if (tab === 'document') {
        const plan = i.lifecycle === 'withdrawn' ? i.source : (data.tasks.find(task=>task.id===i.task)?.plan || i.source)
        $('detailContent').innerHTML = `<p class="original">${escape(plan)}</p>` + markdown(data.documents[plan].text)
    } else {
        const related = data.items.filter(x=>x.id!==i.id && x.lifecycle!=='withdrawn' && (i.lines.some(n=>x.lines.includes(n)) || (i.superseded_by||[]).includes(x.id)))
        const conflict = related.some(x=>state.items[x.id].decision==='accepted') && s.decision==='accepted'
        const changes = i.edits?.length ? (i.kind==='reply' ? markdown(evidence.body) : '') + `<p>${escape(i.reason||'')}</p><p class="location-help">点击任一对照框，中间原文将定位到该处。</p>`+i.edits.map((e,index)=>`<article class="change-pair" data-edit="${index}" role="button" tabindex="0" aria-label="定位修改 ${index+1}：${Number.isInteger(e.line) ? '第 '+e.line+' 行' : escape(e.line)}" aria-pressed="false"><strong>修改 ${index+1} · ${Number.isInteger(e.line) ? '第 '+e.line+' 行' : escape(e.line)}<span class="locate-hint">点击定位 ↗</span></strong><div class="before"><small>${escape(e.before_label || '原文')}</small><pre>${escape(e.before)}</pre></div><div class="after"><small>${escape(e.after_label || '改为')}</small><pre>${escape(e.after||'（删除此处）')}</pre></div></article>`).join('')+(i.kind==='reply' ? '' : `<details><summary>展开依据与完整说明</summary>${markdown(evidence.body)}</details>`) : markdown(evidence.body)
        const assets = (i.assets||[]).map(a=>`<figure class="asset"><a href="${escape(a.url)}" target="_blank"><img src="${escape(a.url)}" alt="${escape(a.title)}"></a><figcaption>${escape(a.title)}</figcaption></figure>`).join('')
        const execution = evidence.execution || ''
        $('detailContent').innerHTML = (withdrawn ? `<div class="withdrawn-note"><strong>已撤回旧方案</strong><p>${escape(i.withdrawal_reason)}</p><p>这不是新的作者拒绝记录。原有评论、审批及版本均在“讨论历史”中。</p></div>` : '') + (execution ? `<div class="execution">${escape(execution)}</div>` : '') + assets + changes +
            (related.length ? `<details class="related"><summary>${conflict?'本处有多项已接受提案，落实前需合并':'关联修改点（'+related.length+'）'}</summary>${related.map(x=>`<button data-related="${x.id}">${escape(x.task+' · '+x.title)} · ${names[state.items[x.id].decision]}</button>`).join('')}</details>` : '') + `<p style="color:#7b8e82">接受不自动改写论文；实际落实与核验另行记录。</p>`
        $('detailContent').querySelectorAll('[data-related]').forEach(b=>b.onclick=()=>{$('status').value='all'; group='all'; select(b.dataset.related)})
        $('detailContent').querySelectorAll('[data-edit]').forEach(box => {
            box.onclick = () => {
                if (!window.getSelection()?.toString()) locateEdit(Number(box.dataset.edit))
            }
            box.onkeydown = event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    locateEdit(Number(box.dataset.edit))
                }
            }
        })
        updateEditFocus()
    }
}

async function save(action,value='') {
    if (busy || !selected) return
    const i = data.items.find(x=>x.id===selected)
    const text = $('comment').value.trim()
    if (action==='comment' && !text) return toast('请先填写评论')
    busy = true
    const buttons = [...document.querySelectorAll('.compose button')]
    buttons.forEach(b=>b.disabled=true)
    $('saveStatus').textContent = '正在保存到项目…'
    try {
        const response = await fetch('/api/event',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({entity:i.id,version:i.version,revision:state.items[i.id].revision,action,value,text,actor:$('actor').value})})
        const result = await response.json()
        if (!response.ok) throw Error(result.error)
        state = result
        $('comment').value = ''
        delete drafts[i.id]
        localStorage.setItem(draftKey,JSON.stringify(drafts))
        renderNavigation()
        renderDetail()
        $('saveStatus').textContent = '已保存 · '+new Date().toLocaleTimeString('zh-CN')
        toast(action==='comment'?'评论已保存':`已记录：${names[value]}`)
    } catch (error) {
        $('saveStatus').textContent = '未保存。输入已保留。'
        toast(error.message)
    } finally {
        busy=false
        buttons.forEach(b=>b.disabled=false)
        renderDetail()
    }
}

function showSourceStatus(loaded, changed = false) {
    $('warning').hidden = !loaded.stale.length && !changed
    if (loaded.stale.length) {
        const files = loaded.stale.map(path => path.split(/[\\/]/).pop()).join('、')
        $('warning').textContent = `检测到文件内容变化：${files}。需核对并更新审阅材料后再审批；已有审批和评论均保留。`
    } else if (changed) {
        $('warning').textContent = '新版审阅材料已准备好。请点击“刷新历史”加载后继续审阅；已有审批、评论及未提交的输入均保留。'
    } else {
        $('warning').textContent = ''
    }
}

async function checkFreshness() {
    if (!data || busy || checkingFreshness || document.hidden) return
    checkingFreshness = true
    const current = data
    try {
        const response = await fetch('/api/data')
        if (!response.ok) throw Error('无法读取审阅台状态')
        const loaded = await response.json()
        // 只更新提示，不切换提案、定位或讨论草稿；手动刷新时丢弃较早的检查结果。
        if (data !== current) return
        const changed = JSON.stringify(loaded.data) !== JSON.stringify(current)
        if (!changed) token = loaded.token
        showSourceStatus(loaded, changed)
    } catch (error) {
        // 短暂断连不能作为文件已恢复一致的证据。
        if (data === current && $('warning').hidden) {
            $('warning').hidden = false
            $('warning').textContent = '暂时无法连接本地审阅台，正在重试。未提交的输入仍保留。'
        }
    } finally {
        checkingFreshness = false
    }
}

async function refresh() {
    const loaded = await (await fetch('/api/data')).json()
    data = loaded.data
    token = loaded.token
    state = await (await fetch('/api/state')).json()
    showSourceStatus(loaded)
    applyFilter()
}

function nextItem() {
    const list = filtered()
    const start = list.findIndex(i=>i.id===selected)
    const ordered = [...list.slice(start+1),...list.slice(0,start+1)]
    const next = ordered.find(i=>i.id!==selected && needsReview(i))
    if (next) select(next.id)
    else toast('当前筛选下没有其他待审项')
}

async function init() {
    const result = await (await fetch('/api/data')).json()
    data=result.data
    $('projectName').textContent = data.project
    draftKey = 'revision-desk-drafts-' + result.workspace_id
    drafts = JSON.parse(localStorage.getItem(draftKey) || '{}')
    token=result.token
    showSourceStatus(result)
    $('page').innerHTML=data.pages.map(p=>`<option value="${p.number}">第 ${p.number} 页</option>`).join('')
    $('actor').value=localStorage.getItem('revision-desk-actor')||'作者'
    $('actor').onchange=()=>localStorage.setItem('revision-desk-actor',$('actor').value)
    const initial=decodeURIComponent(location.hash.slice(1))
    await refresh()
    if (data.items.find(i=>i.id===initial)?.lifecycle==='withdrawn') $('status').value='withdrawn'
    else if (data.items.find(i=>i.id===initial)?.review_deferred) {group=data.items.find(i=>i.id===initial).task;$('status').value='reply'}
    else if (data.items.some(i=>i.id===initial) && !needsReview(data.items.find(i=>i.id===initial))) $('status').value='all'
    if (data.items.some(i=>i.id===initial)) select(initial)
    else applyFilter()
    $('search').oninput=applyFilter
    $('status').onchange=applyFilter
    $('byTask').onclick=()=>{grouping='task';group='all';applyFilter()}
    $('byIssue').onclick=()=>{grouping='issue';group='all';applyFilter()}
    $('documentVersion').onchange=()=>switchDocument($('documentVersion').value)
    $('page').onchange=()=>{page=Number($('page').value);mode='pdf';focusedEdit=null;updateEditFocus();renderPaper();$('paperScroll').scrollTop=0}
    $('prevPage').onclick=()=>{page=Math.max(1,page-1);mode='pdf';focusedEdit=null;updateEditFocus();renderPaper();$('paperScroll').scrollTop=0}
    $('nextPage').onclick=()=>{page=Math.min(readerDocument().pages.length,page+1);mode='pdf';focusedEdit=null;updateEditFocus();renderPaper();$('paperScroll').scrollTop=0}
    $('zoom').onclick=()=>{zoom=zoom===100?150:zoom===150?200:100;renderPaper();scrollToOriginal($('paper').querySelector('.edit-target'))}
    $('batch').onclick=()=>{batchItems=filtered().filter(i=>i.lifecycle!=='withdrawn' && !i.review_deferred && i.review_stage!=='addressed');if(!batchItems.length)return toast('旧提案仅保留历史，请选择当前返修项');$('batchList').innerHTML=batchItems.map(i=>`<label><input type="checkbox" value="${i.id}" checked> ${escape(i.task+' · '+i.title)} <small>${names[state.items[i.id].decision]}</small></label>`).join('');$('batchDialog').showModal()}
    $('batchCancel').onclick=()=>$('batchDialog').close()
    $('batchCommit').onclick=async()=>{
        const ids=[...$('batchList').querySelectorAll('input:checked')].map(x=>x.value)
        if(!ids.length)return toast('请至少选择一项')
        const items=batchItems.filter(i=>ids.includes(i.id)).map(i=>({id:i.id,version:i.version,revision:state.items[i.id].revision}))
        $('batchCommit').disabled=true
        try{
            const response=await fetch('/api/batch',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({items,value:$('batchValue').value,text:$('batchNote').value,actor:$('actor').value})})
            const result=await response.json()
            if(!response.ok)throw Error(result.error)
            state=result
            $('batchDialog').close()
            renderNavigation()
            renderDetail()
            toast('已逐项保存 '+items.length+' 项审批')
        }catch(error){toast(error.message)}finally{$('batchCommit').disabled=false}
    }
    $('mode').onclick=()=>{
        mode=mode==='pdf'?'source':'pdf'
        if (mode==='pdf' && focusedEdit && !focusedEdit.anchor) focusedEdit=null
        updateEditFocus()
        renderPaper()
        const i=data.items.find(x=>x.id===selected)
        scrollToOriginal($('paper').querySelector('.edit-target') || (mode==='source' && i ? $('line-'+itemLines(i)[0]) : $('paper').querySelector('.pin.selected')))
    }
    document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;renderDetail()})
    $('postComment').onclick=()=>save('comment')
    for(const [id,value] of [['accept','accepted'],['reject','rejected'],['reopen','pending'],['needsRevision','needs_revision'],['needsInfo','needs_info']]) $(id).onclick=()=>save('decision',value)
    $('applied').onclick=()=>save('implementation','applied')
    $('verified').onclick=()=>save('implementation','verified')
    $('nextItem').onclick=nextItem
    $('refresh').onclick=()=>refresh().then(()=>toast('历史已刷新')).catch(e=>toast(e.message))
    $('comment').oninput=()=>{drafts[selected]=$('comment').value;localStorage.setItem(draftKey,JSON.stringify(drafts))}
    $('copyLink').onclick=async()=>{await navigator.clipboard.writeText(location.href);toast('此修改点链接已复制')}
    $('export').onclick=async()=>{
        const result=await(await fetch('/api/export')).json()
        const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}))
        const link=document.createElement('a')
        link.href=url
        link.download='revision-desk-review-'+new Date().toISOString().slice(0,10)+'.json'
        link.click()
        setTimeout(()=>URL.revokeObjectURL(url),5000)
    }
    $('closeGroup').onclick=async()=>{
        const s=state.groups[group]
        const value=s.closed?'open':'closed'
        if(value==='closed'&&!s.eligible) return toast('需先处理全部关联提案，并登记已接受项的核验，才能验收此组')
        const note=prompt('请填写本组验收证据或重新打开的原因：')
        if(!note?.trim()) return
        const response=await fetch('/api/event',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({entity:group,version:s.version,revision:s.revision,action:'closure',value,text:note,actor:$('actor').value})})
        const result=await response.json()
        if(!response.ok)return toast(result.error)
        state=result
        renderNavigation()
        toast('已记录本组验收状态')
    }
    document.addEventListener('keydown',e=>{
        if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return
        if(e.key==='j') nextItem()
    })
    window.addEventListener('focus', checkFreshness)
    document.addEventListener('visibilitychange', checkFreshness)
    setInterval(checkFreshness, 30000)
}
init().catch(e=>{toast('载入失败：'+e.message);$('empty').textContent='请确认本地服务正在运行，然后刷新页面。'})
