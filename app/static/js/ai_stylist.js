'use strict';
/**
 * AURA STUDIO - Tính năng AI: Stylist chat, tính size, phối đồ.
 * Phụ thuộc app.js (U, Modal, Actions, App, CFG).
 */

const AIStylist = {
  state: { messages: [], typing: false, contextId: null, suggestions: [], sizeProductId: null },

  init() {
    this.state.messages.push({
      role: 'assistant', welcome: true,
      content: '👋 **Xin chào! Mình là AURA Stylist AI.**\n\nMình có thể gợi ý outfit đi làm, dự tiệc, đi biển, hẹn hò, tính size theo chiều cao – cân nặng, hoặc tìm đồ theo ngân sách. Bạn muốn mặc đồ cho dịp nào?',
      engine: '', products: [],
    });
    this.state.suggestions = [
      '💼 Tư vấn đồ đi làm thanh lịch', '🥂 Set đồ đi tiệc sang trọng', '🌊 Trang phục đi biển mùa hè',
      '☕ Outfit hẹn hò cuối tuần', '📏 Cao 1m65 nặng 55kg mặc size gì?', '💸 Áo dưới 300k',
    ];
    U.$('#chat-form').addEventListener('submit', e => { e.preventDefault(); this.sendFromInput(); });
    U.$('#size-form').addEventListener('submit', e => { e.preventDefault(); this.calcSize(); });
    this.renderSuggestions();
    this.renderMessages();
  },

  /* ---------- Chat ---------- */
  open(contextId = null) {
    if (contextId !== null) this.setContext(contextId);
    Modal.open('chat-drawer');
    this.renderMessages();
    setTimeout(() => U.$('#chat-input').focus(), 150);
  },

  setContext(id) {
    this.state.contextId = id;
    const box = U.$('#chat-context');
    const p = id && App.state.catalog[id];
    if (!p) { box.classList.add('hidden'); return; }
    box.innerHTML = `
      <div class="mb-1 flex items-center justify-between rounded-xl border border-violet-200 bg-violet-50 p-2 text-xs">
        <div class="flex min-w-0 items-center gap-2">
          ${U.img(p.images[0], p.name, 'h-10 w-8 flex-shrink-0 rounded object-cover')}
          <div class="min-w-0"><span class="text-zinc-500">Đang tư vấn cho:</span><strong class="line-clamp-1 block text-zinc-900">${U.esc(p.name)}</strong></div>
        </div>
        <button data-action="clear-context" class="px-1 text-zinc-400 hover:text-zinc-700" aria-label="Bỏ ngữ cảnh">✕</button>
      </div>`;
    box.classList.remove('hidden');
  },

  consult(id) {
    this.open(id);
    this.send('Hãy tư vấn cách phối đồ và đánh giá sản phẩm này giúp tôi!');
  },

  sendFromInput() {
    const input = U.$('#chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    this.send(text);
  },

  async send(text) {
    const st = this.state;
    if (st.typing || !text) return;
    // Lịch sử gửi lên server: bỏ lời chào, chỉ role+content, 8 tin gần nhất (chưa gồm tin hiện tại)
    const history = st.messages.filter(m => !m.welcome && !m.error).slice(-8).map(m => ({ role: m.role, content: m.content.slice(0, 4000) }));
    st.messages.push({ role: 'user', content: text });
    st.typing = true;
    this.renderMessages();
    try {
      const d = await U.api('/api/ai/chat', {
        method: 'POST', timeout: 70000,
        body: { user_message: text, messages: history, context_product_id: st.contextId },
      });
      App.cache(d.recommended_products);
      st.messages.push({ role: 'assistant', content: d.reply, engine: d.engine_used, products: d.recommended_products });
      if (d.quick_suggestions && d.quick_suggestions.length) { st.suggestions = d.quick_suggestions; this.renderSuggestions(); }
    } catch (e) {
      st.messages.push({ role: 'assistant', error: true, content: `Rất tiếc, mình chưa trả lời được lúc này: ${e.message}. Bạn thử lại nhé!`, products: [] });
    } finally {
      st.typing = false;
      this.renderMessages();
    }
  },

  renderSuggestions() {
    U.$('#chat-suggestions').innerHTML = this.state.suggestions.map(s =>
      `<button data-action="chat-suggest" data-text="${U.esc(s.replace(/^\S+\s/u, ''))}" class="chip">${U.esc(s)}</button>`).join('');
  },

  /** Markdown tối giản, ESCAPE TRƯỚC rồi mới thêm thẻ (chống XSS từ người dùng lẫn từ LLM). */
  format(text) {
    return U.esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*(?!\s)(.+?)\*(?=[\s).,!?]|$)/g, '$1<em>$2</em>')
      .replace(/\n/g, '<br>');
  },

  productMini(p) {
    return `
      <div class="flex gap-2 rounded-xl border border-zinc-200 bg-white p-2 transition hover:border-violet-300">
        ${U.img(p.images[0], p.name, 'h-16 w-12 flex-shrink-0 rounded-lg bg-zinc-100 object-cover')}
        <div class="flex min-w-0 flex-1 flex-col justify-between">
          <div><h4 class="line-clamp-2 text-[11px] font-semibold text-zinc-900">${U.esc(p.name)}</h4>
            <span class="text-[11px] font-bold text-violet-700">${U.vnd(p.final_price)}</span></div>
          <button data-action="quickview" data-id="${p.id}" ${p.in_stock ? '' : 'disabled'} class="btn btn-dark btn-sm mt-1 !py-1">${p.in_stock ? 'Chọn size' : 'Hết hàng'}</button>
        </div>
      </div>`;
  },

  renderMessages() {
    const box = U.$('#chat-messages');
    let html = this.state.messages.map(m => {
      if (m.role === 'user') {
        return `<div class="mb-4 flex justify-end"><div class="chat-bubble-user max-w-[85%] px-4 py-2.5 text-sm shadow-md">${this.format(m.content)}</div></div>`;
      }
      return `
      <div class="mb-4 flex items-start gap-2.5">
        <div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-tr from-violet-600 to-indigo-600 text-xs text-white shadow">✨</div>
        <div class="min-w-0 max-w-[90%] flex-1">
          <div class="chat-bubble-ai px-4 py-3 text-sm leading-relaxed shadow-sm ${m.error ? '!border-red-200 !bg-red-50' : ''}">
            ${this.format(m.content)}
            ${m.products && m.products.length ? `
              <div class="mt-3 border-t border-zinc-200/70 pt-3">
                <span class="mb-2 block text-[11px] font-bold uppercase tracking-wider text-zinc-500">Sản phẩm gợi ý</span>
                <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">${m.products.map(p => this.productMini(p)).join('')}</div>
              </div>` : ''}
          </div>
          ${m.engine ? `<div class="ml-1 mt-1 flex items-center gap-1.5 text-[10px] text-zinc-400"><span class="h-1.5 w-1.5 rounded-full bg-violet-500"></span>${U.esc(m.engine)}</div>` : ''}
        </div>
      </div>`;
    }).join('');
    if (this.state.typing) {
      html += `<div class="mb-4 flex items-center gap-2.5"><div class="flex h-7 w-7 animate-pulse items-center justify-center rounded-full bg-gradient-to-tr from-violet-600 to-indigo-600 text-xs text-white">✨</div>
        <div class="chat-bubble-ai flex items-center gap-2 px-4 py-2.5 text-xs text-zinc-500">
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-600"></span><span class="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-600 [animation-delay:.2s]"></span><span class="h-1.5 w-1.5 animate-bounce rounded-full bg-violet-600 [animation-delay:.4s]"></span>
          <span class="ml-1 font-medium">Stylist đang soạn gợi ý...</span></div></div>`;
    }
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
    U.$('#chat-send').disabled = this.state.typing;
  },

  /* ---------- Tính size ---------- */
  openSize(productId = null) {
    this.state.sizeProductId = productId || null;
    const p = productId && App.state.catalog[productId];
    const box = U.$('#size-product');
    if (p) {
      box.innerHTML = `<div class="flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 p-2 text-xs">
        ${U.img(p.images[0], p.name, 'h-10 w-8 rounded object-cover')}
        <div class="min-w-0"><div class="line-clamp-1 font-bold">${U.esc(p.name)}</div>
        <div class="text-zinc-500">Size đang bán: ${p.sizes.map(U.esc).join(', ')}</div></div></div>`;
      box.classList.remove('hidden');
    } else box.classList.add('hidden');

    const me = U.store.get('aura_body', null);
    if (me) {
      U.$('#size-height').value = me.height; U.$('#size-weight').value = me.weight; U.$('#size-gender').value = me.gender;
      const r = U.$(`input[name="fit"][value="${me.fit}"]`); if (r) r.checked = true;
    }
    U.$('#size-result').classList.add('hidden');
    U.$('#size-error').classList.add('hidden');
    Modal.open('size-modal');
  },

  async calcSize() {
    const err = U.$('#size-error'), res = U.$('#size-result'), btn = U.$('#size-submit');
    const height = Number(U.$('#size-height').value), weight = Number(U.$('#size-weight').value);
    const gender = U.$('#size-gender').value;
    const fit = (U.$('input[name="fit"]:checked') || {}).value || 'regular';
    err.classList.add('hidden');
    if (!(height >= 100 && height <= 230)) return this.sizeError('Chiều cao cần nằm trong khoảng 100–230 cm');
    if (!(weight >= 25 && weight <= 200)) return this.sizeError('Cân nặng cần nằm trong khoảng 25–200 kg');

    btn.disabled = true; btn.textContent = 'Đang phân tích...';
    try {
      const d = await U.api('/api/ai/size-recommend', { method: 'POST',
        body: { height_cm: height, weight_kg: weight, gender, fit_preference: fit, product_id: this.state.sizeProductId } });
      U.store.set('aura_body', { height, weight, gender, fit });
      const qvOpen = Modal.isOpen('quickview-modal') && App.state.qv.product && App.state.qv.product.id === this.state.sizeProductId;
      res.innerHTML = `
      <div class="rounded-xl border border-violet-200 bg-gradient-to-br from-violet-50 to-indigo-50 p-4">
        <div class="flex items-center justify-between gap-3">
          <div><span class="text-[11px] font-bold uppercase tracking-wider text-violet-700">Size khuyến nghị</span>
            <div class="mt-0.5 break-words text-3xl font-extrabold text-violet-900">${U.esc(d.recommended_size)}</div>
            ${d.alternative_size ? `<div class="text-[11px] text-zinc-600">Size sát bên cân nhắc: <b>${U.esc(d.alternative_size)}</b></div>` : ''}</div>
          <div class="text-right text-[11px] text-zinc-500">BMI <b>${d.bmi}</b><br>${U.esc(d.bmi_category)}</div>
        </div>
        <p class="mt-3 border-t border-violet-200/60 pt-2.5 text-xs leading-relaxed text-zinc-700">${U.esc(d.fit_advice)}</p>
        ${d.note ? `<p class="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">${U.esc(d.note)}</p>` : ''}
        <div class="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
          ${Object.entries(d.measurements_estimated).map(([k, v]) => `<div class="rounded-lg border border-violet-100 bg-white/80 p-2"><span class="block text-zinc-400">${U.esc(k)}</span><strong class="mt-0.5 block font-semibold text-zinc-800">${U.esc(v)}</strong></div>`).join('')}
        </div>
        ${qvOpen ? `<button data-action="size-apply" data-size="${U.esc(d.recommended_size)}" class="btn btn-ai mt-3 w-full">Chọn size ${U.esc(d.recommended_size)} cho sản phẩm này</button>` : ''}
      </div>`;
      res.classList.remove('hidden');
    } catch (e) { this.sizeError(e.message); }
    finally { btn.disabled = false; btn.textContent = 'Phân tích size'; }
  },

  sizeError(msg) { const e = U.$('#size-error'); e.textContent = msg; e.classList.remove('hidden'); },

  /* ---------- Phối đồ ---------- */
  outfit: { productId: null, occasion: null, gender: null, variant: 0, data: null },
  OCCASIONS: [['', '✨ Tự động'], ['cong_so', '💼 Công sở'], ['du_tiec', '🥂 Dạ tiệc'], ['di_bien', '🌊 Đi biển'], ['hen_ho', '☕ Hẹn hò'], ['thu_dong', '❄️ Thu đông']],
  GENDERS: [['', 'Tất cả'], ['nu', 'Nữ'], ['nam', 'Nam']],

  openOutfit(productId = null) {
    Object.assign(this.outfit, { productId: productId || null, occasion: null, gender: null, variant: 0, data: null });
    Modal.close('quickview-modal');
    Modal.open('outfit-modal');
    this.loadOutfit();
  },

  async loadOutfit() {
    const o = this.outfit, box = U.$('#outfit-body');
    box.innerHTML = `<div class="py-20 text-center"><div class="inline-block h-8 w-8 animate-spin rounded-full border-2 border-violet-600 border-t-transparent"></div>
      <p class="mt-3 text-sm font-medium text-zinc-600">Stylist AI đang phối set đồ...</p></div>`;
    try {
      o.data = await U.api('/api/ai/outfit', { method: 'POST',
        body: { product_id: o.productId, occasion: o.occasion, gender: o.gender, variant: o.variant } });
      App.cache(o.data.items);
      this.renderOutfit();
    } catch (e) {
      box.innerHTML = `<div class="p-8 text-center"><p class="text-sm text-red-600">${U.esc(e.message)}</p>
        <button data-action="outfit-reshuffle" class="btn btn-ai mt-4">Thử lại</button>
        <button data-action="close-modal" data-target="outfit-modal" class="btn btn-outline mt-4">Đóng</button></div>`;
    }
  },

  renderOutfit() {
    const o = this.outfit, d = o.data;
    const chip = (list, cur, action, key) => list.map(([v, l]) =>
      `<button data-action="${action}" data-${key}="${v}" class="chip ${(cur || '') === v ? 'chip-on' : ''}">${l}</button>`).join('');
    const saved = d.total_price - d.discounted_combo_price;
    U.$('#outfit-body').innerHTML = `
    <div class="p-5 sm:p-6">
      <div class="flex items-start justify-between gap-3 border-b border-zinc-100 pb-4">
        <div><span class="mb-1 inline-block rounded-full bg-violet-100 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-violet-800">AI Curated Look</span>
          <h2 class="text-xl font-bold text-zinc-900">${U.esc(d.outfit_name)}</h2>
          <p class="mt-1 text-xs text-zinc-500">${U.esc(d.style_concept)}</p></div>
        <button data-action="close-modal" data-target="outfit-modal" class="icon-btn flex-shrink-0" aria-label="Đóng">✕</button>
      </div>
      ${o.productId ? '' : `<div class="mt-3 space-y-2">
        <div class="no-scrollbar flex gap-2 overflow-x-auto">${chip(this.OCCASIONS, o.occasion, 'outfit-occasion', 'occasion')}</div>
        <div class="flex items-center gap-2 text-xs text-zinc-500">Dành cho ${chip(this.GENDERS, o.gender, 'outfit-gender', 'gender')}</div></div>`}
      <div class="mt-3 flex items-start gap-2.5 rounded-xl border border-violet-100 bg-violet-50/60 p-3 text-xs leading-relaxed text-zinc-700">
        <span class="text-base">💡</span><div><strong>Lời khuyên phối đồ:</strong> ${U.esc(d.style_tip)}</div></div>

      <div class="mt-5 grid grid-cols-2 gap-3 ${({ 1: 'sm:grid-cols-1', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3' })[d.items.length] || 'sm:grid-cols-4'}">
        ${d.items.map((it, i) => `
        <div class="relative flex flex-col justify-between rounded-xl border border-zinc-200 bg-zinc-50 p-2.5">
          <span class="absolute left-2 top-2 z-10 rounded bg-zinc-900 px-2 py-0.5 text-[10px] font-semibold text-white">${o.productId && i === 0 ? 'Món của bạn' : 'Món #' + (i + 1)}</span>
          <div class="mb-2 aspect-[3/4] overflow-hidden rounded-lg bg-white">${U.img(it.images[0], it.name, 'h-full w-full object-cover')}</div>
          <div><span class="text-[10px] uppercase tracking-wider text-zinc-400">${U.esc(it.category_name)}</span>
            <h3 class="mt-0.5 line-clamp-2 text-xs font-bold text-zinc-900">${U.esc(it.name)}</h3>
            <div class="mt-1 text-xs font-bold text-violet-700">${U.vnd(it.final_price)}</div></div>
        </div>`).join('')}
      </div>

      <div class="mt-5 flex flex-col items-center justify-between gap-4 rounded-xl bg-zinc-900 p-4 text-white sm:flex-row">
        <div>
          <span class="text-xs text-zinc-400 line-through">Tổng lẻ: ${U.vnd(d.total_price)}</span>
          <div class="mt-0.5 flex flex-wrap items-baseline gap-2">
            <span class="text-xl font-bold">${U.vnd(d.discounted_combo_price)}</span>
            <span class="rounded-full bg-emerald-900/60 px-2 py-0.5 text-xs font-semibold text-emerald-300">Tiết kiệm ${U.vnd(saved)} (${d.discount_percentage}%)</span>
          </div>
        </div>
        <div class="flex w-full gap-2 sm:w-auto">
          <button data-action="outfit-reshuffle" class="btn flex-1 border border-zinc-600 text-white hover:bg-zinc-800 sm:flex-none">🔄 Phối lại</button>
          <button id="outfit-add" data-action="outfit-add" class="btn btn-ai flex-1 uppercase sm:flex-none">🛒 Thêm trọn bộ</button>
        </div>
      </div>
      <p class="mt-2 text-center text-[11px] text-zinc-400">Giảm ${d.discount_percentage}% được áp dụng tự động trong giỏ hàng khi đủ bộ.</p>
    </div>`;
  },

  async addOutfit() {
    const d = this.outfit.data;
    if (!d) return;
    const btn = U.$('#outfit-add');
    btn.disabled = true;
    const me = U.store.get('aura_body', null);
    try {
      // Có số đo đã lưu -> AI chọn size cho từng món; chưa có -> chọn size giữa dải (đổi được trong giỏ)
      const sizes = await Promise.all(d.items.map(async it => {
        if (me) {
          try {
            const r = await U.api('/api/ai/size-recommend', { method: 'POST',
              body: { height_cm: me.height, weight_kg: me.weight, gender: me.gender, fit_preference: me.fit, product_id: it.id } });
            if (it.sizes.includes(r.recommended_size)) return r.recommended_size;
          } catch { /* dùng size mặc định bên dưới */ }
        }
        return it.sizes[Math.floor((it.sizes.length - 1) / 2)];
      }));
      let added = 0;
      d.items.forEach((it, i) => {
        if (App.addToCart(it.id, sizes[i], it.colors[0].name, 1, d.combo_token, { open: false, silent: true })) added++;
      });
      if (!added) return;
      Modal.close('outfit-modal');
      Modal.open('cart-drawer');
      U.toast(me ? `Đã thêm ${added} món với size theo số đo của bạn` : `Đã thêm ${added} món. Bạn có thể đổi size ngay trong giỏ hàng`);
    } finally { btn.disabled = false; }
  },
};

Object.assign(Actions, {
  'open-chat': d => AIStylist.open(d.id || null),
  'toggle-chat': () => (Modal.isOpen('chat-drawer') ? Modal.close('chat-drawer') : AIStylist.open()),
  consult: d => AIStylist.consult(d.id),
  'clear-context': () => { AIStylist.state.contextId = null; U.$('#chat-context').classList.add('hidden'); },
  'chat-suggest': d => AIStylist.send(d.text),
  'open-size': d => AIStylist.openSize(d.productId),
  'size-apply': d => { App.qvSelect('size', d.size); Modal.close('size-modal'); U.toast(`Đã chọn size ${d.size} theo tư vấn AI`); },
  'open-outfit': d => AIStylist.openOutfit(d.productId),
  'outfit-occasion': d => { AIStylist.outfit.occasion = d.occasion || null; AIStylist.outfit.variant = 0; AIStylist.loadOutfit(); },
  'outfit-gender': d => { AIStylist.outfit.gender = d.gender || null; AIStylist.outfit.variant = 0; AIStylist.loadOutfit(); },
  'outfit-reshuffle': () => { AIStylist.outfit.variant = (AIStylist.outfit.variant + 1) % 6; AIStylist.loadOutfit(); },
  'outfit-add': () => AIStylist.addOutfit(),
});

document.addEventListener('DOMContentLoaded', () => AIStylist.init());
