/* Define once in the supported CUA persistent REPL. No fetch, filesystem, cookies or hidden state. */
function createBatchHelpers({cacheDays = 7, now = () => new Date()} = {}) {
  const cache = new Map(), pending = new Map(), acknowledged = new Set(), links = new Map();
  const canonical = value => JSON.stringify(value && typeof value === 'object'
    ? Array.isArray(value) ? value.map(v => JSON.parse(canonical(v)))
      : Object.fromEntries(Object.keys(value).filter(k=>value[k]!==undefined).sort().map(k => [k, JSON.parse(canonical(value[k]))])) : value === undefined ? null : value);
  const cleanUrl = value => { const u = new URL(value); u.search = ''; u.hash = ''; return u.href; };
  const sanitize = value => {
    if (Array.isArray(value)) return value.map(sanitize);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => {
      if (/cookie|password|authorization|token/i.test(k)) throw new Error('credential field rejected');
      return [k, sanitize(v)];
    }));
    if (typeof value === 'string') {
      if (/^https?:\/\//.test(value)) return cleanUrl(value);
      if (/xsec_token=|web_session/i.test(value)) throw new Error('credential content rejected');
    }
    return value;
  };
  const stage = row => { const safe = sanitize(row), key = canonical(safe); if (!acknowledged.has(key)) pending.set(key,safe); };
  const cached = id => { const p=cache.get(id); const age=p ? now()-new Date(p.observed_at) : Infinity; return p && p.reviewed === true && age >= 0 && age <= cacheDays*86400000 ? p : null; };
  const stopReason = text => /验证码|captcha|访问过于频繁|访问频繁|无访问权限|403 forbidden|登录后才能|请登录后|login required|access denied/i.test(text || '') ? 'access_control_stop' : null;
  const users = note => [...new Set((note.comments || []).filter(c => c.level === 1 && c.is_author === false && !c.parent_id && c.user_id).map(c=>c.user_id))];
  async function readProfile(tab, id) {
    await tab.playwright.locator('.user-info').waitFor({state:'visible',timeoutMs:10000});
    return tab.playwright.evaluate(({id,observed_at}) => {
      const text = s => document.querySelector(s)?.textContent?.trim() || '';
      const actual = location.pathname.split('/user/profile/')[1]?.split('/')[0];
      if (actual !== id) throw new Error('profile identity mismatch');
      const icons=[...document.querySelectorAll('.user-tags .gender use')].map(e=>e.getAttribute('href') || e.getAttribute('xlink:href') || '');
      const values=[...new Set(icons.flatMap(x=>/#female\b/.test(x)?['female']:/#male\b/.test(x)?['male']:[]))];
      const gender=values.length===1?values[0]:null;
      return {kind:'audience_dom',user_id:id,reviewed:true,observed_at,gender,gender_evidence:icons.join(' '),tags_text:text('.user-tags'),desc:gender==='female'?text('.user-desc'):null,recent_titles:gender==='female'?[...document.querySelectorAll('#userPostedFeeds a.title')].slice(0,6).map(e=>e.textContent.trim()):[]};
    }, {id,observed_at:now().toISOString()});
  }
  async function profiles(tab, requests, reader = readProfile) {
    if (requests.length < 1 || requests.length > 12) throw new Error('batch size must be 1–12');
    let visits=0,hits=0,failed=0,stop=null;
    for (const request of requests) {
      if (cached(request.id)) { hits++; continue; }
      try {
        const url=new URL(request.url);
        if (url.protocol !== 'https:' || url.hostname !== 'www.xiaohongshu.com' || !url.pathname.startsWith('/user/profile/') || url.pathname.split('/')[3] !== request.id) throw new Error('actual profile link required');
        await tab.goto(request.url); visits++;
        const control=await tab.playwright.evaluate(() => document.body.innerText.slice(0,10000));
        stop=stopReason(control); if(stop) break;
        const row=await reader(tab,request.id);
        if (row.user_id !== request.id)throw new Error('profile identity mismatch');
        if (row.reviewed !== true) { failed++; continue; }
        stage(row); cache.set(request.id,row);
      } catch(error) {
        stop=stopReason(String(error)) || 'unexpected_error_inspect'; failed++; break;
      }
    }
    return {requested:requests.length,profile_visits:visits,cache_hits:hits,failed,stop,pending_records:pending.size};
  }
  function profileQueue(note, extend = false) {
    const selected=users(note).slice(0,extend?30:8);
    return {ids:selected.filter(id=>!cached(id)),selected_count:selected.length,comment_expand:selected.length<(extend?30:8),target:extend?30:8};
  }
  async function readNote(tab, noteId, creatorId, prior) {
    if(!prior || prior.note_id!==noteId || prior.creator_id!==creatorId)throw new Error('matching prior note capture required; first captures use verified manual reader');
    await tab.playwright.locator('#noteContainer').waitFor({state:'visible',timeoutMs:10000});
    const row = await tab.playwright.evaluate(({noteId,creatorId,observed_at}) => {
      const text=(root,s)=>root.querySelector(s)?.textContent?.trim() || '';
      const root=document.querySelector('#noteContainer');
      const actual=location.pathname.match(/\/(?:explore|search_result|discovery\/item)\/([^/?#]+)/)?.[1];
      const authorLink=root.querySelector(':scope > .author a.name')?.getAttribute('href');
      const author=authorLink?.match(/\/user\/profile\/([^/?#]+)/)?.[1];
      if(actual!==noteId || author!==creatorId) throw new Error('note identity mismatch');
      const comments=[...root.querySelectorAll('.comment-item')].map(e=>{
        const link=e.querySelector('.author a.name')?.getAttribute('href') || '';
        const user_id=link.match(/\/user\/profile\/([^/?#]+)/)?.[1] || null;
        const id=e.getAttribute('id')?.replace(/^comment-/, '');
        const sub=e.classList.contains('comment-item-sub');
        return {id,user_id,profile_link:link?new URL(link,location.href).href:null,level:sub?2:1,parent_id:sub?e.closest('.parent-comment')?.querySelector('.comment-item')?.getAttribute('id')?.replace(/^comment-/, '') || null:null,is_author:user_id?user_id===creatorId:null,content:text(e,'.content'),date_raw:text(e,'.date'),intents:[]};
      }).filter(c=>c.id);
      return {kind:'note_dom',note_id:noteId,creator_id:creatorId,observed_at,title:text(root,'#detail-title'),desc:text(root,'#detail-desc'),date_raw:text(root,'.date'),likes_raw:text(root,'.like-wrapper .count'),collects_raw:text(root,'.collect-wrapper .count'),comments_raw:text(root,'.chat-wrapper .count'),comment_header:text(root,'.comments-container .total'),comments,end_visible:false,expand_buttons:[...root.querySelectorAll('.show-more')].map(e=>e.textContent.trim())};
    }, {noteId,creatorId,observed_at:now().toISOString()});
    for(const comment of row.comments){if(comment.profile_link && comment.user_id) links.set(comment.user_id,comment.profile_link);delete comment.profile_link;}
    const comments=mergeComments(prior.comments || [],row.comments);
    stage({...prior,...row,comments}); return {comments:comments.length,distinct_external_users:users({comments}).length,pending_records:pending.size};
  }
  // Caller passes only IDs after verifying the 8-person screen; no gender inference here.
  return {profiles, readProfile, readNote, profileQueue, cached, stage, stopReason,
    seedCache:rows=>{for(const row of rows){if(row.kind==='audience_dom' && row.user_id && (!cache.has(row.user_id) || new Date(row.observed_at)>=new Date(cache.get(row.user_id).observed_at)))cache.set(row.user_id,row);}},
    requests:ids=>ids.filter(id=>links.has(id)).map(id=>({id,url:links.get(id)})),
    delta:()=>[...pending.values()],
    ack:rows=>{for(const row of rows){const key=canonical(sanitize(row)); if(pending.has(key)){pending.delete(key);acknowledged.add(key);}}},
    summary:()=>({cached_profiles:cache.size,pending_records:pending.size})};
}
function mergeComments(prior, current) {
  const result=new Map(prior.map(c=>[c.id,{...c}]));
  for(const c of current){
    const old=result.get(c.id), merged={...old,...c};
    if(old){
      for(const key of ['intents','interaction_target','intent_annotation'])if(old[key]!==undefined)merged[key]=old[key];
      if(old.content!==c.content){
        merged.annotation_review_required=true;merged.previous_content=old.content;
        merged.previous_annotation={intents:old.intents || [],interaction_target:old.interaction_target || 'unknown',intent_annotation:old.intent_annotation || null};
        merged.intents=[];merged.interaction_target='unknown';merged.intent_annotation=null;
      }
    }
    result.set(c.id,merged);
  }
  return [...result.values()];
}
if (typeof module !== 'undefined') module.exports = {createBatchHelpers,mergeComments};
