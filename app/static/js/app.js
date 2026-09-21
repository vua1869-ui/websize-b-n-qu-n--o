'use strict';
/**
 * AURA STUDIO - lõi giao diện: catalog, Flash Sale, giỏ hàng, thanh toán, Live.
 * Nguyên tắc: MỌI dữ liệu chèn vào HTML đều qua U.esc(); không dùng onclick chứa JSON;
 * giá tiền hiển thị/thanh toán luôn lấy từ server (/api/orders/quote).
 */

/* ===================== Tiện ích ===================== */
const U = {
  $: (s, r = document) => r.querySelector(s),
  $$: (s, r = document) => Array.from(r.querySelectorAll(s)),
  esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  },
  vnd: n => Number(n || 0).toLocaleString('vi-VN') + '₫',
  debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; },
  store: {
    get(k, def) { try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch { return def; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* bỏ qua (chế độ riêng tư) */ } },
  },
  async api(url, { method = 'GET', body, timeout = 30000 } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const res = await fetch(url, {
        method, signal: ctrl.signal,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = null;
      try { data = await res.json(); } catch { /* không phải JSON */ }
      if (!res.ok) throw new Error((data && typeof data.detail === 'string' && data.detail) || `Lỗi máy chủ (${res.status})`);
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw new Error('Máy chủ phản hồi quá lâu, vui lòng thử lại');
      if (e instanceof TypeError) throw new Error('Không kết nối được máy chủ');
      throw e;
    } finally { clearTimeout(timer); }
  },
  toast(msg, type = 'ok') {
    const root = U.$('#toast-root');
    if (!root) return;
    while (root.children.length >= 3) root.firstChild.remove();
    const el = document.createElement('div');
    el.className = 'toast pointer-events-auto flex max-w-sm items-center gap-2 rounded-xl border px-4 py-3 text-xs font-bold text-white shadow-2xl '
      + (type === 'error' ? 'border-red-400 bg-red-700' : 'border-zinc-700 bg-zinc-900');
    el.textContent = (type === 'error' ? '⚠️ ' : '✓ ') + msg;
    root.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  },
  img(src, alt, cls = '') {
    return `<img src="${U.esc(src)}" alt="${U.esc(alt)}" class="${cls}" loading="lazy" />`;
  },
};

// Ảnh lỗi (link hỏng/mất mạng) -> ảnh thay thế thay vì icon vỡ
document.addEventListener('error', e => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && !t.dataset.fallback) {
    t.dataset.fallback = '1';
    t.src = '/static/img/placeholder.svg';
  }
}, true);

/* ===================== Modal / Drawer ===================== */
const Modal = {
  stack: [],
  open(id) {
    const el = U.$('#' + id);
    if (!el) return;
    el.classList.remove('hidden');
    this.stack = this.stack.filter(x => x !== id);
    this.stack.push(id);
    el.style.zIndex = 50 + this.stack.length; // modal mở sau luôn nằm trên modal mở trước
    document.body.classList.add('modal-open');
  },
  close(id) {
    const el = U.$('#' + id);
    if (el) el.classList.add('hidden');
    this.stack = this.stack.filter(x => x !== id);
    if (!this.stack.length) document.body.classList.remove('modal-open');
  },
  isOpen: id => { const el = U.$('#' + id); return !!el && !el.classList.contains('hidden'); },
};
document.addEventListener('keydown', e => { if (e.key === 'Escape' && Modal.stack.length) Modal.close(Modal.stack[Modal.stack.length - 1]); });
document.addEventListener('mousedown', e => { // bấm ra ngoài khung -> đóng
  if (e.target.matches && e.target.matches('[data-modal]')) Modal.close(e.target.id);
});

/* ===================== Điều phối sự kiện (thay cho onclick inline) ===================== */
const Actions = {};
document.addEventListener('click', e => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const fn = Actions[el.dataset.action];
  if (fn) fn(el.dataset, el, e);
});

/* ===================== Ứng dụng ===================== */
const CFG = {
  freeShip: Number(document.body.dataset.freeShip || 299000),
  shipFee: Number(document.body.dataset.shipFee || 30000),
  combo: Number(document.body.dataset.combo || 15),
  maxQty: 10,
};

const App = {
  state: {
    catalog: {}, categories: [], products: [], vouchers: [], videos: [], notice: '',
    savedVouchers: U.store.get('aura_vouchers_v2', []),
    cart: U.store.get('aura_cart_v2', []),
    wishlist: U.store.get('aura_wishlist', []),
    filters: { category: 'all', gender: 'all', sort: 'popular', search: '', min: null, max: null, wishlist: false },
    flash: { endsAt: 0, skew: 0, items: [], reloading: false },
    qv: { product: null, size: null, color: null, qty: 1 },
    quote: null, quoteError: '', voucher: '',
  },
  _seq: { products: 0, quote: 0 },

  async init() {
    this.bindEvents();
    this.updateBadges();
    this.renderSortButtons();
    await Promise.all([this.loadCategories(), this.loadProducts(), this.loadFlash(), this.loadVouchers(), this.loadVideos()]);
    this.sanitizeCart();
    this.refreshCart();
  },

  bindEvents() {
    const input = U.$('#search-input');
    input.addEventListener('input', U.debounce(() => { this.state.filters.search = input.value.trim(); this.loadProducts(); }, 300));
    U.$('#search-form').addEventListener('submit', e => {
      e.preventDefault();
      this.state.filters.search = input.value.trim();
      this.loadProducts();
      U.$('#catalog').scrollIntoView({ behavior: 'smooth' });
    });
    U.$('#filter-gender').addEventListener('change', e => { this.state.filters.gender = e.target.value; this.loadProducts(); });
    U.$('#filter-price').addEventListener('change', e => {
      const [a, b] = (e.target.value || '-').split('-');
      this.state.filters.min = a ? Number(a) : null;
      this.state.filters.max = b ? Number(b) : null;
      this.loadProducts();
    });
    U.$('#checkout-form').addEventListener('submit', e => { e.preventDefault(); this.submitOrder(); });
    U.$('#live-form').addEventListener('submit', e => { e.preventDefault(); Live.send(); });
  },

  cache(list) { list.forEach(p => { this.state.catalog[p.id] = p; }); },

  /* ---------- Danh mục ---------- */
  async loadCategories() {
    try {
      this.state.categories = await U.api('/api/categories');
    } catch { this.state.categories = []; }
    const icons = this.state.categories.map(c => `
      <button data-action="set-category" data-cat="${U.esc(c.id)}" class="group flex flex-col items-center">
        <span class="flex h-12 w-12 items-center justify-center rounded-2xl bg-zinc-100 text-xl transition group-hover:scale-105 group-hover:bg-zinc-200">${c.icon}</span>
        <span class="mt-1.5 text-[11px] font-bold text-zinc-800">${U.esc(c.name)}</span>
      </button>`).join('');
    U.$('#quick-nav').insertAdjacentHTML('beforeend', icons);
    this.renderTabs();
  },

  renderTabs() {
    const tabs = [{ id: 'all', name: 'Tất cả' }, { id: 'flash_sale', name: '🔥 Flash Sale' }, ...this.state.categories];
    U.$('#category-tabs').innerHTML = tabs.map(t => {
      const on = this.state.filters.category === t.id;
      return `<button role="tab" aria-selected="${on}" data-action="set-category" data-cat="${U.esc(t.id)}"
        class="whitespace-nowrap border-b-2 px-3.5 py-2 text-xs font-bold transition ${on ? 'border-brand-600 text-brand-600' : 'border-transparent text-zinc-600 hover:text-zinc-900'}">${U.esc(t.name)}</button>`;
    }).join('');
  },

  renderSortButtons() {
    U.$$('.sort-btn').forEach(b => {
      const on = b.dataset.sort === this.state.filters.sort;
      b.classList.toggle('btn-primary', on);
      b.classList.toggle('btn-outline', !on);
    });
  },

  /* ---------- Sản phẩm ---------- */
  async loadProducts() {
    const grid = U.$('#products-grid');
    const seq = ++this._seq.products;
    const f = this.state.filters;
    const params = new URLSearchParams({ sort: f.sort });
    if (f.category && f.category !== 'all') params.set('category', f.category);
    if (f.gender !== 'all') params.set('gender', f.gender);
    if (f.min != null) params.set('min_price', f.min);
    if (f.max != null) params.set('max_price', f.max);
    if (f.search) params.set('search', f.search);

    try {
      let list = await U.api('/api/products?' + params);
      this.state.notice = '';
      if (!list.length && f.search) { // không khớp chính xác -> gợi ý theo ngữ nghĩa
        const near = await U.api('/api/ai/search?' + new URLSearchParams({ q: f.search, limit: 6 }));
        list = near;
        this.state.notice = `Không có sản phẩm khớp chính xác với "${f.search}". Có thể bạn sẽ thích:`;
      }
      if (seq !== this._seq.products) return; // đã có yêu cầu mới hơn
      this.state.products = list;
      this.cache(list);
      this.renderProducts();
    } catch (err) {
      if (seq !== this._seq.products) return;
      grid.innerHTML = `<div class="col-span-full py-12 text-center text-sm text-red-600">${U.esc(err.message)}
        <br><button data-action="reset-filters" class="btn btn-soft mt-3">Thử lại</button></div>`;
    }
  },

  productCard(p) {
    const wished = this.state.wishlist.includes(p.id);
    const sold = p.sold_count > 1000 ? (p.sold_count / 1000).toFixed(1) + 'k' : p.sold_count;
    const out = !p.in_stock;
    const hasSecond = p.images && p.images.length > 1;
    return `
    <article class="product-card group relative flex flex-col overflow-hidden rounded-lg bg-white">
      <div class="relative aspect-square cursor-pointer overflow-hidden bg-zinc-100" data-action="quickview" data-id="${p.id}">
        ${U.img(p.images[0], p.name, 'h-full w-full object-cover transition-all duration-500 ' + (hasSecond ? 'group-hover:opacity-0 group-hover:scale-105' : 'group-hover:scale-105') + (out ? ' opacity-50' : ''))}
        ${hasSecond ? U.img(p.images[1], p.name, 'absolute inset-0 h-full w-full object-cover opacity-0 transition-all duration-500 group-hover:opacity-100 group-hover:scale-105' + (out ? ' opacity-50' : '')) : ''}
        ${p.discount_percent > 0 ? `<span class="absolute right-0 top-0 rounded-bl-lg bg-brand-600 px-2 py-1 text-[11px] font-extrabold text-white">-${p.discount_percent}%</span>` : ''}
        ${p.is_new ? '<span class="tag absolute left-2 top-2 bg-emerald-600 text-white">MỚI</span>' : ''}
        ${out ? '<span class="absolute inset-0 flex items-center justify-center bg-black/30 text-sm font-black uppercase text-white">Hết hàng</span>' : ''}
        ${hasSecond ? `<span class="pointer-events-none absolute bottom-2 left-2 rounded bg-black/50 backdrop-blur-sm px-1.5 py-0.5 text-[9px] font-bold text-white opacity-0 transition-opacity group-hover:opacity-100 sm:block">${p.images.length} góc ảnh</span>` : ''}
        <button data-action="wishlist" data-id="${p.id}" aria-label="${wished ? 'Bỏ yêu thích' : 'Yêu thích'}" aria-pressed="${wished}"
          class="absolute bottom-2 right-2 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-white/90 shadow transition hover:scale-110 ${wished ? 'text-rose-500' : 'text-zinc-500'}">
          <svg class="h-4 w-4" fill="${wished ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"/></svg>
        </button>
      </div>
      <div class="flex flex-1 cursor-pointer flex-col justify-between p-2.5" data-action="quickview" data-id="${p.id}">
        <div>
          <h3 class="line-clamp-2 text-xs leading-relaxed text-zinc-900 group-hover:text-brand-600">${U.esc(p.name)}</h3>
          <div class="mt-1.5 flex flex-wrap gap-1">
            ${p.flash_sale ? '<span class="tag border border-orange-200 bg-orange-50 text-brand-600">🔥 Flash Sale</span>' : ''}
            ${p.is_hot ? '<span class="tag border border-rose-200 bg-rose-50 text-rose-600">Bán chạy</span>' : ''}
          </div>
        </div>
        <div class="mt-3">
          <div class="flex items-baseline gap-1.5">
            <span class="text-sm font-extrabold leading-none text-brand-600 sm:text-base">${U.vnd(p.final_price)}</span>
            ${p.discount_percent > 0 ? `<span class="text-[10px] text-zinc-400 line-through">${U.vnd(p.original_price)}</span>` : ''}
          </div>
          <div class="mt-2 flex items-center justify-between border-t border-zinc-100 pt-2 text-[11px] text-zinc-500">
            <span><span class="text-amber-400">★</span> <b class="text-zinc-700">${p.rating}</b> • Đã bán ${sold}</span>
          </div>
        </div>
      </div>
      <div class="flex gap-1.5 px-2.5 pb-2.5">
        <button data-action="consult" data-id="${p.id}" class="btn btn-ai-soft btn-sm flex-1" title="Tư vấn phối đồ cùng AI">✨ AI Stylist</button>
        <button data-action="quickview" data-id="${p.id}" ${out ? 'disabled' : ''} class="btn btn-primary btn-sm">Mua</button>
      </div>
    </article>`;
  },

  renderProducts() {
    const grid = U.$('#products-grid');
    const f = this.state.filters;
    let list = this.state.products;
    if (f.wishlist) list = list.filter(p => this.state.wishlist.includes(p.id));

    let label = `${list.length} sản phẩm`;
    if (f.wishlist) label = `❤️ Yêu thích: ${list.length} sản phẩm`;
    else if (f.category === 'flash_sale') label = `🔥 Flash Sale: ${list.length} sản phẩm`;
    U.$('#products-count').textContent = label;

    if (!list.length) {
      grid.innerHTML = `<div class="col-span-full rounded-2xl border border-zinc-200 py-16 text-center">
        <div class="text-4xl">🔍</div>
        <h3 class="mt-2 text-sm font-bold text-zinc-800">Không tìm thấy sản phẩm phù hợp</h3>
        <p class="mt-1 text-xs text-zinc-500">Hãy thử đổi bộ lọc hoặc hỏi Stylist AI để được gợi ý.</p>
        <div class="mt-4 flex justify-center gap-2">
          <button data-action="reset-filters" class="btn btn-primary">Xóa bộ lọc</button>
          <button data-action="open-chat" class="btn btn-ai-soft">✨ Hỏi Stylist AI</button>
        </div></div>`;
      return;
    }
    const notice = this.state.notice
      ? `<p class="col-span-full rounded-lg bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">${U.esc(this.state.notice)}</p>` : '';
    grid.innerHTML = notice + list.map(p => this.productCard(p)).join('');
  },

  setCategory(cat) {
    this.state.filters.category = cat;
    this.state.filters.wishlist = false;
    this.renderTabs();
    this.loadProducts();
  },

  resetFilters() {
    this.state.filters = { category: 'all', gender: 'all', sort: 'popular', search: '', min: null, max: null, wishlist: false };
    U.$('#search-input').value = '';
    U.$('#filter-gender').value = 'all';
    U.$('#filter-price').value = '';
    this.renderTabs();
    this.renderSortButtons();
    this.loadProducts();
  },

  toggleWishlist(id) {
    const w = this.state.wishlist;
    const i = w.indexOf(id);
    if (i > -1) { w.splice(i, 1); U.toast('Đã bỏ khỏi yêu thích'); } else { w.push(id); U.toast('Đã lưu vào yêu thích'); }
    U.store.set('aura_wishlist', w);
    this.updateBadges();
    this.renderProducts();
  },

  /* ---------- Flash Sale (đếm ngược theo giờ server) ---------- */
  async loadFlash() {
    try {
      const d = await U.api('/api/flash-sale');
      this.state.flash.items = d.items;
      this.state.flash.endsAt = d.ends_at;
      this.state.flash.skew = d.server_now - Date.now();
      this.cache(d.items);
      this.renderFlash();
    } catch {
      U.$('#flash-sale-carousel').innerHTML = '<p class="py-6 text-xs text-zinc-500">Không tải được Flash Sale.</p>';
    }
    if (!this._timer) this._timer = setInterval(() => this.tick(), 1000);
    this.tick();
  },

  tick() {
    const f = this.state.flash;
    const diff = Math.max(0, f.endsAt - (Date.now() + f.skew));
    const pad = n => String(n).padStart(2, '0');
    U.$('#fs-hours').textContent = pad(Math.floor(diff / 3600000));
    U.$('#fs-mins').textContent = pad(Math.floor(diff % 3600000 / 60000));
    U.$('#fs-secs').textContent = pad(Math.floor(diff % 60000 / 1000));
    if (diff <= 0 && f.endsAt && !f.reloading) { // sang khung mới
      f.reloading = true;
      this.loadFlash().finally(() => { f.reloading = false; });
    }
  },

  renderFlash() {
    U.$('#flash-sale-carousel').innerHTML = this.state.flash.items.map(p => {
      const sold = p.stock_total - p.stock;
      const pct = Math.min(100, Math.round(sold / p.stock_total * 100));
      return `
      <div data-action="quickview" data-id="${p.id}" class="group flex w-44 flex-shrink-0 cursor-pointer flex-col overflow-hidden rounded-xl border border-orange-100 bg-white transition hover:border-brand-600 hover:shadow-md sm:w-48">
        <div class="relative aspect-square overflow-hidden bg-zinc-100">
          ${U.img(p.images[0], p.name, 'h-full w-full object-cover transition-all duration-500 ' + (p.images.length > 1 ? 'group-hover:opacity-0 group-hover:scale-105' : 'group-hover:scale-105'))}
          ${p.images.length > 1 ? U.img(p.images[1], p.name, 'absolute inset-0 h-full w-full object-cover opacity-0 transition-all duration-500 group-hover:opacity-100 group-hover:scale-105') : ''}
          <span class="absolute right-0 top-0 rounded-bl-lg bg-brand-600 px-2 py-1 text-[11px] font-extrabold text-white">-${p.discount_percent}%</span>
        </div>
        <div class="flex flex-1 flex-col justify-between p-2.5">
          <h3 class="line-clamp-1 text-xs font-semibold text-zinc-800 group-hover:text-brand-600">${U.esc(p.name)}</h3>
          <div class="mt-2">
            <div class="text-base font-extrabold leading-tight text-brand-600">${U.vnd(p.final_price)}</div>
            <div class="text-[11px] text-zinc-400 line-through">${U.vnd(p.original_price)}</div>
          </div>
          <div class="flash-progress mt-2.5"><div class="fill" style="width:${pct}%"></div>
            <div class="label">${p.stock > 0 ? `<span class="flame-anim mr-1">🔥</span>Đã bán ${sold}` : 'Hết hàng'}</div></div>
        </div>
      </div>`;
    }).join('');
  },

  /* ---------- Voucher ---------- */
  async loadVouchers() {
    try { this.state.vouchers = await U.api('/api/vouchers'); } catch { this.state.vouchers = []; }
    this.renderVouchers();
  },

  renderVouchers() {
    U.$('#vouchers-list').innerHTML = this.state.vouchers.map(v => {
      const saved = this.state.savedVouchers.includes(v.code);
      return `
      <div class="voucher-ticket flex items-center justify-between gap-3 rounded-lg p-3">
        <div class="flex items-center gap-2.5">
          <div class="flex h-11 w-11 flex-shrink-0 flex-col items-center justify-center rounded-lg border border-brand-100 bg-brand-50 text-brand-600">
            <span class="text-xs font-black">${U.esc(v.code.slice(0, 4))}</span><span class="text-[8px] font-bold uppercase">Mã</span></div>
          <div class="min-w-0">
            <div class="text-xs font-bold text-brand-600">${U.esc(v.discount_display)}</div>
            <div class="truncate text-[11px] font-medium text-zinc-600">${U.esc(v.title)}</div>
            <div class="text-[10px] text-zinc-400">${v.min_order ? 'Đơn từ ' + U.vnd(v.min_order) : 'Mọi đơn hàng'} • ${U.esc(v.expire_in)}</div>
          </div>
        </div>
        <button data-action="claim-voucher" data-code="${U.esc(v.code)}" ${saved ? 'disabled' : ''}
          class="btn btn-sm flex-shrink-0 rounded-full ${saved ? 'bg-zinc-100 text-zinc-400' : 'btn-primary'}">${saved ? 'Đã lưu' : 'Lưu mã'}</button>
      </div>`;
    }).join('');
  },

  claimVoucher(code) {
    if (this.state.savedVouchers.includes(code)) return;
    this.state.savedVouchers.push(code);
    U.store.set('aura_vouchers_v2', this.state.savedVouchers);
    this.renderVouchers();
    U.toast(`Đã lưu mã ${code}. Dùng khi thanh toán.`);
  },

  /* ---------- Video lookbook ---------- */
  async loadVideos() {
    try { this.state.videos = await U.api('/api/videos'); } catch { this.state.videos = []; }
    this.cache(this.state.videos.map(v => v.tagged_product));
    U.$('#video-grid').innerHTML = this.state.videos.map(v => `
      <div class="reel-card group flex flex-col justify-between" data-action="video" data-id="${U.esc(v.id)}" role="button" tabindex="0" aria-label="${U.esc(v.title)}">
        ${U.img(v.poster_image, v.title, 'absolute inset-0 h-full w-full object-cover transition duration-500 group-hover:scale-105')}
        <div class="absolute inset-0 bg-gradient-to-b from-black/20 via-transparent to-black/80"></div>
        <div class="relative z-10 flex items-center justify-between p-3 text-[10px] text-white">
          <span class="rounded-full bg-rose-600 px-2 py-0.5 font-bold">Thử đồ</span>
          <span class="rounded-full bg-black/40 px-2 py-0.5 backdrop-blur">❤️ ${U.esc(v.likes)}</span>
        </div>
        <div class="relative z-10 mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white/30 text-white shadow-lg backdrop-blur transition group-hover:scale-110">
          <svg class="ml-0.5 h-6 w-6 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
        <div class="relative z-10 space-y-2 p-3 text-white">
          <p class="line-clamp-2 text-xs font-medium leading-relaxed">${U.esc(v.title)}</p>
          <div class="gold-bag flex items-center justify-between gap-2 rounded-xl p-2">
            <span class="line-clamp-1 text-xs font-bold text-zinc-950">🛍️ ${U.esc(v.tagged_product.name)}</span>
            <span class="flex-shrink-0 text-xs font-black text-rose-700">${U.vnd(v.tagged_product.final_price)}</span>
          </div>
        </div>
      </div>`).join('');
  },

  openVideo(id) {
    const v = this.state.videos.find(x => x.id === id);
    if (!v) return;
    const p = v.tagged_product;
    const media = v.video_url
      ? `<video src="${U.esc(v.video_url)}" poster="${U.esc(v.poster_image)}" class="absolute inset-0 h-full w-full object-cover" controls autoplay muted playsinline loop></video>`
      : U.img(v.poster_image, v.title, 'absolute inset-0 h-full w-full object-cover');
    U.$('#video-body').innerHTML = `
      <div class="relative flex aspect-[9/16] max-h-[92vh] flex-col justify-between overflow-hidden rounded-3xl border border-zinc-800 bg-black p-4 text-white shadow-2xl">
        ${media}
        <div class="pointer-events-none absolute inset-0 bg-gradient-to-t from-black via-transparent to-black/40"></div>
        <div class="relative z-10 flex items-center justify-between text-xs">
          <div class="flex items-center gap-2">${U.img(v.author_avatar, v.author, 'h-8 w-8 rounded-full border border-white object-cover')}<b>${U.esc(v.author)}</b></div>
          <button data-action="close-modal" data-target="video-modal" class="flex h-8 w-8 items-center justify-center rounded-full bg-black/60" aria-label="Đóng">✕</button>
        </div>
        <div class="relative z-10 space-y-3 pb-1">
          <p class="text-xs text-zinc-200">${U.esc(v.title)}</p>
          <div class="flex items-center gap-3 rounded-2xl bg-white p-3 text-zinc-900 shadow-2xl">
            ${U.img(p.images[0], p.name, 'h-14 w-12 flex-shrink-0 rounded-lg object-cover')}
            <div class="min-w-0 flex-1">
              <h3 class="line-clamp-1 text-xs font-bold">${U.esc(p.name)}</h3>
              <div class="flex items-baseline gap-2"><span class="text-xs font-extrabold text-brand-600">${U.vnd(p.final_price)}</span>
                <span class="text-[10px] text-zinc-400 line-through">${U.vnd(p.original_price)}</span></div>
            </div>
            <button data-action="quickview" data-id="${p.id}" data-from="video-modal" class="btn btn-primary btn-sm whitespace-nowrap">Mua ngay</button>
          </div>
        </div>
      </div>`;
    Modal.open('video-modal');
  },

  /* ---------- Xem nhanh sản phẩm ---------- */
  openQuickView(id, from) {
    const p = this.state.catalog[id];
    if (!p) return;
    if (from) Modal.close(from);
    const multi = p.sizes.length > 1;
    this.state.qv = { product: p, size: multi ? null : p.sizes[0], color: p.colors[0].name, qty: 1, currentImgIdx: 0 };
    const out = !p.in_stock;
    U.$('#qv-body').innerHTML = `
    <div class="grid grid-cols-1 gap-6 p-5 sm:p-6 md:grid-cols-2">
      <div class="space-y-3">
        <div class="relative aspect-square overflow-hidden rounded-2xl border border-zinc-200 bg-zinc-100 group">
          ${U.img(p.images[0], p.name, 'h-full w-full object-cover transition duration-300').replace('<img ', '<img id="qv-main-img" ')}
          ${p.images.length > 1 ? `
          <button data-action="qv-prev-img" class="absolute left-2.5 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-white/90 text-zinc-800 shadow-md backdrop-blur-sm transition hover:bg-white hover:scale-110 active:scale-95" aria-label="Xem ảnh trước">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7"/></svg>
          </button>
          <button data-action="qv-next-img" class="absolute right-2.5 top-1/2 -translate-y-1/2 flex h-9 w-9 items-center justify-center rounded-full bg-white/90 text-zinc-800 shadow-md backdrop-blur-sm transition hover:bg-white hover:scale-110 active:scale-95" aria-label="Xem ảnh kế tiếp">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"/></svg>
          </button>
          <span id="qv-img-badge" class="absolute bottom-2.5 right-2.5 rounded-full bg-black/60 backdrop-blur-sm px-2.5 py-0.5 text-[11px] font-bold text-white tracking-wide">1 / ${p.images.length}</span>
          ` : ''}
        </div>
        ${p.images.length > 1 ? `<div class="flex gap-2 overflow-x-auto pb-1" id="qv-thumbs">${p.images.map((img, i) => `
          <button data-action="qv-img" data-idx="${i}" data-src="${U.esc(img)}" class="qv-thumb-btn h-14 w-14 flex-shrink-0 overflow-hidden rounded-lg border-2 transition ${i === 0 ? 'border-brand-600 ring-2 ring-brand-600/30' : 'border-zinc-200 hover:border-brand-500'}" aria-label="Xem góc ảnh ${i+1}">
            ${U.img(img, '', 'h-full w-full object-cover')}</button>`).join('')}</div>` : ''}
      </div>
      <div class="flex flex-col justify-between">
        <div>
          <div class="text-xs text-zinc-500">${U.esc(p.category_name)} • ${U.esc(p.style)}</div>
          <h2 class="mt-1.5 pr-8 text-base font-bold leading-snug text-zinc-900 sm:text-lg">${U.esc(p.name)}</h2>
          <div class="mt-2 flex items-center gap-3 text-xs text-zinc-500">
            <span class="font-bold text-brand-600">${p.rating} ★</span><span>•</span><span>${p.reviews_count} đánh giá</span><span>•</span><span>Đã bán ${p.sold_count}</span>
          </div>
          <div class="mt-3 flex flex-wrap items-baseline gap-3 rounded-xl border border-brand-100 bg-brand-50/70 p-3.5">
            <span class="text-2xl font-black text-brand-600">${U.vnd(p.final_price)}</span>
            ${p.discount_percent > 0 ? `<span class="text-xs text-zinc-400 line-through">${U.vnd(p.original_price)}</span>
              <span class="rounded-full bg-brand-600 px-2 py-0.5 text-[10px] font-bold text-white">-${p.discount_percent}%</span>` : ''}
            ${p.flash_sale ? '<span class="tag bg-amber-100 text-amber-800">🔥 Giá Flash Sale</span>' : ''}
          </div>
          <p class="mt-3 text-xs leading-relaxed text-zinc-600">${U.esc(p.description)}</p>
          <p class="mt-1.5 text-xs text-zinc-500"><b>Chất liệu:</b> ${U.esc(p.material)}</p>

          <div class="mt-4">
            <div class="mb-1.5 text-xs font-semibold text-zinc-700">Màu sắc: <span id="qv-color-label" class="font-bold text-brand-600">${U.esc(p.colors[0].name)}</span></div>
            <div class="flex flex-wrap gap-2" id="qv-colors">
              ${p.colors.map((c, i) => `<button data-action="qv-color" data-color="${U.esc(c.name)}" title="${U.esc(c.name)}" aria-label="${U.esc(c.name)}" aria-pressed="${i === 0}"
                class="h-7 w-7 rounded-full border-2 transition ${i === 0 ? 'border-brand-600 ring-2 ring-brand-200' : 'border-zinc-200'}" style="background-color:${U.esc(c.hex)}"></button>`).join('')}
            </div>
          </div>

          <div class="mt-4">
            <div class="mb-1.5 flex items-center justify-between">
              <span class="text-xs font-semibold text-zinc-700">Kích cỡ: <span id="qv-size-label" class="font-bold text-brand-600">${multi ? '' : U.esc(p.sizes[0])}</span></span>
              <button data-action="open-size" data-product-id="${p.id}" class="text-xs font-bold text-violet-700 hover:underline">✨ AI gợi ý size chuẩn</button>
            </div>
            <div class="flex flex-wrap gap-2" id="qv-sizes">
              ${p.sizes.map(s => `<button data-action="qv-size" data-size="${U.esc(s)}" aria-pressed="${!multi}"
                class="rounded border px-3 py-1 text-xs font-semibold transition ${!multi ? 'border-brand-600 bg-brand-600 text-white' : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400'}">${U.esc(s)}</button>`).join('')}
            </div>
            <p id="qv-size-hint" class="mt-1.5 hidden text-xs font-semibold text-red-600">Vui lòng chọn size trước khi thêm vào giỏ.</p>
          </div>
        </div>

        <div class="mt-6 space-y-2 border-t border-zinc-100 pt-4">
          <p class="text-xs ${p.stock <= 10 ? 'font-semibold text-red-600' : 'text-zinc-500'}">${out ? 'Sản phẩm đã hết hàng' : p.stock <= 10 ? `Chỉ còn ${p.stock} sản phẩm!` : `Còn ${p.stock} sản phẩm`}</p>
          <div class="flex gap-2">
            <div class="flex items-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-50">
              <button data-action="qv-qty" data-delta="-1" class="px-3 py-1.5 font-bold text-zinc-600 hover:bg-zinc-100" aria-label="Giảm">−</button>
              <span id="qv-qty" class="min-w-6 text-center text-xs font-bold">1</span>
              <button data-action="qv-qty" data-delta="1" class="px-3 py-1.5 font-bold text-zinc-600 hover:bg-zinc-100" aria-label="Tăng">+</button>
            </div>
            <button data-action="qv-add" ${out ? 'disabled' : ''} class="btn btn-soft flex-1">🛒 Thêm vào giỏ</button>
            <button data-action="qv-buy" ${out ? 'disabled' : ''} class="btn btn-primary flex-1">Mua ngay</button>
          </div>
          <button data-action="open-outfit" data-product-id="${p.id}" class="btn btn-ai-soft w-full">✨ AI phối trọn bộ cùng món này (giảm ${CFG.combo}%)</button>
        </div>
      </div>
    </div>`;
    Modal.open('quickview-modal');
  },

  qvSetImage(idx) {
    const qv = this.state.qv;
    if (!qv || !qv.product || !qv.product.images || !qv.product.images.length) return;
    const len = qv.product.images.length;
    idx = (idx % len + len) % len;
    qv.currentImgIdx = idx;
    const mainImg = U.$('#qv-main-img');
    if (mainImg) mainImg.src = qv.product.images[idx];
    const badge = U.$('#qv-img-badge');
    if (badge) badge.textContent = `${idx + 1} / ${len}`;
    const thumbs = document.querySelectorAll('.qv-thumb-btn');
    thumbs.forEach((btn, i) => {
      if (i === idx) {
        btn.className = 'qv-thumb-btn h-14 w-14 flex-shrink-0 overflow-hidden rounded-lg border-2 transition border-brand-600 ring-2 ring-brand-600/30';
      } else {
        btn.className = 'qv-thumb-btn h-14 w-14 flex-shrink-0 overflow-hidden rounded-lg border-2 transition border-zinc-200 hover:border-brand-500';
      }
    });
  },

  qvSelect(kind, value) {
    const qv = this.state.qv;
    if (kind === 'size') {
      qv.size = value;
      U.$('#qv-size-label').textContent = value;
      U.$('#qv-size-hint').classList.add('hidden');
      U.$$('#qv-sizes button').forEach(b => {
        const on = b.dataset.size === value;
        b.setAttribute('aria-pressed', on);
        b.className = 'rounded border px-3 py-1 text-xs font-semibold transition ' + (on ? 'border-brand-600 bg-brand-600 text-white' : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400');
      });
    } else {
      qv.color = value;
      U.$('#qv-color-label').textContent = value;
      U.$$('#qv-colors button').forEach(b => {
        const on = b.dataset.color === value;
        b.setAttribute('aria-pressed', on);
        b.className = 'h-7 w-7 rounded-full border-2 transition ' + (on ? 'border-brand-600 ring-2 ring-brand-200' : 'border-zinc-200');
      });
    }
  },

  qvAdd(buyNow) {
    const { product: p, size, color, qty } = this.state.qv;
    if (!p) return;
    if (!size) { U.$('#qv-size-hint').classList.remove('hidden'); return; }
    if (!this.addToCart(p.id, size, color, qty, null, { open: !buyNow })) return;
    Modal.close('quickview-modal');
    if (buyNow) this.openCheckout();
  },

  /* ---------- Giỏ hàng ---------- */
  sanitizeCart() {
    const cat = this.state.catalog;
    this.state.cart = this.state.cart.filter(l => {
      const p = cat[l.product_id];
      return p && p.sizes.includes(l.size) && p.colors.some(c => c.name === l.color) && l.quantity > 0;
    });
    this.saveCart();
  },

  saveCart() { U.store.set('aura_cart_v2', this.state.cart); this.updateBadges(); },

  updateBadges() {
    const n = this.state.cart.reduce((s, l) => s + l.quantity, 0);
    U.$$('.cart-badge').forEach(b => { b.textContent = n; b.classList.toggle('hidden', !n); b.classList.toggle('flex', !!n); });
    const w = this.state.wishlist.length;
    U.$$('.wishlist-badge').forEach(b => { b.textContent = w; b.classList.toggle('hidden', !w); b.classList.toggle('flex', !!w); });
  },

  qtyInCart(pid, exceptIdx = -1) {
    return this.state.cart.reduce((s, l, i) => s + (l.product_id === pid && i !== exceptIdx ? l.quantity : 0), 0);
  },

  /** Trả true nếu thêm thành công. */
  addToCart(pid, size, color, qty = 1, comboToken = null, { open = true, silent = false } = {}) {
    const p = this.state.catalog[pid];
    if (!p) { U.toast('Không tìm thấy sản phẩm', 'error'); return false; }
    const cart = this.state.cart;
    const line = cart.find(l => l.product_id === pid && l.size === size && l.color === color && (l.combo_token || null) === comboToken);
    const have = line ? line.quantity : 0;
    const add = Math.min(qty, Math.min(p.stock - this.qtyInCart(pid), CFG.maxQty - have));
    if (add <= 0) {
      U.toast(p.stock <= 0 ? 'Sản phẩm đã hết hàng' : 'Đã đạt số lượng tối đa có thể mua', 'error');
      return false;
    }
    if (line) line.quantity += add;
    else cart.push({ product_id: pid, size, color, quantity: add, combo_token: comboToken });
    this.saveCart();
    this.refreshCart();
    if (open) Modal.open('cart-drawer');
    if (!silent) U.toast(add < qty ? `Chỉ thêm được ${add} sản phẩm (giới hạn tồn kho)` : 'Đã thêm vào giỏ hàng');
    return true;
  },

  changeQty(idx, delta) {
    const l = this.state.cart[idx];
    if (!l) return;
    const p = this.state.catalog[l.product_id];
    const next = l.quantity + delta;
    if (next <= 0) return this.removeLine(idx);
    if (next > CFG.maxQty || this.qtyInCart(l.product_id, idx) + next > p.stock) {
      U.toast(next > CFG.maxQty ? `Tối đa ${CFG.maxQty} sản phẩm mỗi phân loại` : `Chỉ còn ${p.stock} sản phẩm`, 'error');
      return;
    }
    l.quantity = next;
    this.saveCart();
    this.refreshCart();
  },

  removeLine(idx) { this.state.cart.splice(idx, 1); this.saveCart(); this.refreshCart(); },

  changeVariant(idx, kind, value) {
    const l = this.state.cart[idx];
    if (!l) return;
    l[kind] = value;
    const dup = this.state.cart.findIndex((o, i) => i !== idx && o.product_id === l.product_id && o.size === l.size && o.color === l.color && (o.combo_token || null) === (l.combo_token || null));
    if (dup > -1) { // gộp 2 dòng trùng
      this.state.cart[dup].quantity = Math.min(CFG.maxQty, this.state.cart[dup].quantity + l.quantity);
      this.state.cart.splice(idx, 1);
    }
    this.saveCart();
    this.refreshCart();
  },

  cartPayload() {
    return this.state.cart.map(l => ({ product_id: l.product_id, size: l.size, color: l.color, quantity: l.quantity, combo_token: l.combo_token || null }));
  },

  /** Vẽ giỏ ngay bằng dữ liệu local, rồi hỏi server tổng tiền thật. */
  async refreshCart() {
    this.renderCart();
    if (!this.state.cart.length) { this.state.quote = null; this.state.quoteError = ''; this.renderCart(); return; }
    const seq = ++this._seq.quote;
    try {
      const q = await U.api('/api/orders/quote', { method: 'POST', body: { items: this.cartPayload(), voucher_code: this.state.voucher || null } });
      if (seq !== this._seq.quote) return;
      this.state.quote = q;
      this.state.quoteError = '';
    } catch (e) {
      if (seq !== this._seq.quote) return;
      this.state.quote = null;
      this.state.quoteError = e.message;
    }
    this.renderCart();
    if (Modal.isOpen('checkout-modal')) this.renderCheckoutSummary();
  },

  renderCart() {
    const cart = this.state.cart, q = this.state.quote;
    U.$('#cart-count-label').textContent = cart.length ? `(${cart.reduce((s, l) => s + l.quantity, 0)} sản phẩm)` : '';
    const box = U.$('#cart-lines');
    const errEl = U.$('#cart-error');
    const btn = U.$('#cart-checkout');

    if (!cart.length) {
      box.innerHTML = `<div class="py-16 text-center"><div class="text-5xl">🛍️</div>
        <p class="mt-3 text-xs font-semibold text-zinc-600">Giỏ hàng của bạn đang trống</p>
        <button data-action="close-modal" data-target="cart-drawer" class="btn btn-primary mt-4">Tiếp tục mua sắm</button></div>`;
      U.$('#cart-summary').innerHTML = '';
      U.$('#cart-freeship').classList.add('hidden');
      errEl.classList.add('hidden');
      btn.disabled = true;
      return;
    }

    box.innerHTML = cart.map((l, i) => {
      const p = this.state.catalog[l.product_id];
      const ql = q && q.lines[i];
      const opt = (arr, cur) => arr.map(v => `<option value="${U.esc(v)}" ${v === cur ? 'selected' : ''}>${U.esc(v)}</option>`).join('');
      return `
      <div class="flex gap-3 border-b border-zinc-100 py-3">
        ${U.img(p.images[0], p.name, 'h-20 w-16 flex-shrink-0 rounded-lg bg-zinc-100 object-cover')}
        <div class="flex min-w-0 flex-1 flex-col justify-between">
          <div>
            <div class="flex items-start justify-between gap-2">
              <h3 class="line-clamp-2 text-xs font-semibold text-zinc-900">${U.esc(p.name)}</h3>
              <button data-action="cart-remove" data-idx="${i}" class="text-zinc-400 hover:text-rose-500" aria-label="Xóa">✕</button>
            </div>
            <div class="mt-1 flex flex-wrap gap-1.5">
              <select data-cart-variant="size" data-idx="${i}" aria-label="Size" class="rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[11px]">${opt(p.sizes, l.size)}</select>
              <select data-cart-variant="color" data-idx="${i}" aria-label="Màu" class="max-w-[120px] rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[11px]">${opt(p.colors.map(c => c.name), l.color)}</select>
              ${ql && ql.combo ? `<span class="tag bg-violet-100 text-violet-700">Combo -${CFG.combo}%</span>` : ''}
            </div>
          </div>
          <div class="mt-2 flex items-center justify-between">
            <span class="text-xs font-extrabold text-brand-600">${U.vnd(p.final_price)}</span>
            <div class="flex items-center overflow-hidden rounded border border-zinc-200 bg-white">
              <button data-action="cart-qty" data-idx="${i}" data-delta="-1" class="px-2 py-0.5 text-xs hover:bg-zinc-100" aria-label="Giảm">−</button>
              <span class="min-w-5 text-center text-xs font-bold">${l.quantity}</span>
              <button data-action="cart-qty" data-idx="${i}" data-delta="1" class="px-2 py-0.5 text-xs hover:bg-zinc-100" aria-label="Tăng">+</button>
            </div>
          </div>
        </div>
      </div>`;
    }).join('');

    if (this.state.quoteError) {
      errEl.textContent = this.state.quoteError; errEl.classList.remove('hidden');
      U.$('#cart-summary').innerHTML = '';
      U.$('#cart-freeship').classList.add('hidden');
      btn.disabled = true;
      return;
    }
    errEl.classList.add('hidden');

    if (!q) { U.$('#cart-summary').innerHTML = '<div class="skeleton h-16 rounded-lg"></div>'; btn.disabled = true; return; }
    btn.disabled = false;
    U.$('#cart-summary').innerHTML = this.summaryRows(q);

    // Thanh tiến độ freeship
    const after = q.subtotal - q.combo_discount;
    const fs = U.$('#cart-freeship');
    fs.classList.remove('hidden');
    if (after >= CFG.freeShip) {
      fs.innerHTML = '<span class="font-bold text-emerald-700">🎉 Đơn hàng của bạn được miễn phí vận chuyển!</span>';
    } else {
      const pct = Math.round(after / CFG.freeShip * 100);
      fs.innerHTML = `<div class="mb-1 font-semibold text-zinc-700">Mua thêm <b class="text-brand-600">${U.vnd(CFG.freeShip - after)}</b> để được miễn phí vận chuyển</div>
        <div class="h-1.5 overflow-hidden rounded-full bg-zinc-200"><div class="h-full rounded-full bg-emerald-500 transition-all" style="width:${pct}%"></div></div>`;
    }
    if (cart.some(l => l.combo_token) && !q.lines.some(l => l.combo)) {
      fs.innerHTML += '<div class="mt-1.5 font-semibold text-violet-700">Bộ phối đồ chưa đủ món nên chưa được giảm giá combo.</div>';
    }
  },

  summaryRows(q) {
    const row = (a, b, cls = '') => `<div class="flex justify-between ${cls}"><span>${a}</span><span>${b}</span></div>`;
    let h = row('Tạm tính', `<b>${U.vnd(q.subtotal)}</b>`, 'text-zinc-600');
    if (q.combo_discount) h += row('Giảm combo AI phối đồ', '−' + U.vnd(q.combo_discount), 'font-semibold text-violet-700');
    if (q.voucher_discount) h += row(`Voucher ${U.esc(q.voucher_code)}`, '−' + U.vnd(q.voucher_discount), 'font-semibold text-emerald-700');
    const ship = q.shipping_fee - q.shipping_discount;
    h += row('Phí vận chuyển', ship ? U.vnd(ship) : 'Miễn phí', ship ? 'text-zinc-600' : 'font-semibold text-emerald-700');
    h += row('Tổng cộng', U.vnd(q.total), 'border-t border-zinc-200 pt-2 text-sm font-extrabold text-brand-600');
    return h;
  },

  /* ---------- Thanh toán ---------- */
  async openCheckout() {
    if (!this.state.cart.length) { U.toast('Giỏ hàng đang trống', 'error'); return; }
    if (this.state.quoteError) { U.toast(this.state.quoteError, 'error'); return; }
    Modal.close('cart-drawer');
    const me = U.store.get('aura_customer', {});
    U.$('#co-name').value ||= me.name || '';
    U.$('#co-phone').value ||= me.phone || '';
    U.$('#co-address').value ||= me.address || '';
    U.$('#co-error').classList.add('hidden');
    U.$('#co-voucher').value = this.state.voucher || '';
    this.renderSavedVoucherChips();
    this.renderCheckoutSummary();
    Modal.open('checkout-modal');
    if (!this.state.voucher && this.state.savedVouchers.length) await this.autoPickVoucher();
  },

  /** Thử các mã đã lưu và chọn mã có lợi nhất (server tính, không tự đoán). */
  async autoPickVoucher() {
    const items = this.cartPayload();
    const results = await Promise.all(this.state.savedVouchers.map(code =>
      U.api('/api/orders/quote', { method: 'POST', body: { items, voucher_code: code } }).then(q => ({ code, q })).catch(() => null)));
    const best = results.filter(r => r && r.q.voucher_code).sort((a, b) => a.q.total - b.q.total)[0];
    if (best && (!this.state.quote || best.q.total < this.state.quote.total)) {
      U.$('#co-voucher').value = best.code;
      await this.applyVoucher(true);
    }
  },

  renderSavedVoucherChips() {
    U.$('#co-saved-vouchers').innerHTML = this.state.savedVouchers.map(c =>
      `<button type="button" data-action="use-voucher" data-code="${U.esc(c)}" class="chip">🎟️ ${U.esc(c)}</button>`).join('');
  },

  async applyVoucher(silent = false) {
    const code = U.$('#co-voucher').value.trim().toUpperCase();
    this.state.voucher = code;
    await this.refreshCart();
    const msg = U.$('#co-voucher-msg'), q = this.state.quote;
    if (!code) { msg.classList.add('hidden'); return; }
    msg.classList.remove('hidden');
    const ok = q && q.voucher_code === code;
    msg.className = 'mt-1 font-semibold ' + (ok ? 'text-emerald-700' : 'text-red-600');
    msg.textContent = q ? (q.voucher_message || '') : this.state.quoteError;
    if (!ok && !silent) this.state.voucher = '';
  },

  renderCheckoutSummary() {
    const q = this.state.quote;
    U.$('#co-summary').innerHTML = q
      ? q.lines.map(l => `<div class="flex justify-between gap-3 text-zinc-600"><span class="truncate">${l.quantity}× ${U.esc(l.name)} <i class="text-zinc-400">(${U.esc(l.size)}, ${U.esc(l.color)})</i></span><span class="flex-shrink-0">${U.vnd(l.line_total)}</span></div>`).join('')
        + '<div class="my-1 border-t border-zinc-200"></div>' + this.summaryRows(q)
      : '<div class="skeleton h-20 rounded-lg"></div>';
  },

  async submitOrder() {
    const err = U.$('#co-error'), btn = U.$('#co-submit');
    err.classList.add('hidden');
    const body = {
      customer_name: U.$('#co-name').value.trim(),
      customer_phone: U.$('#co-phone').value.trim(),
      customer_address: U.$('#co-address').value.trim(),
      customer_note: U.$('#co-note').value.trim() || null,
      payment_method: (U.$('input[name="payment"]:checked') || {}).value || 'cod',
      items: this.cartPayload(),
      voucher_code: this.state.quote && this.state.quote.voucher_code || null,
    };
    if (body.customer_name.length < 2) return this.checkoutError('Vui lòng nhập họ tên người nhận');
    if (!/^(?:0|\+?84)\d{9}$/.test(body.customer_phone.replace(/[\s.\-]/g, ''))) return this.checkoutError('Số điện thoại không hợp lệ (ví dụ: 0987654321)');
    if (body.customer_address.length < 8) return this.checkoutError('Vui lòng nhập địa chỉ nhận hàng đầy đủ');

    btn.disabled = true; btn.textContent = 'Đang xử lý...';
    try {
      const order = await U.api('/api/orders', { method: 'POST', body });
      U.store.set('aura_customer', { name: body.customer_name, phone: body.customer_phone, address: body.customer_address });
      this.state.cart = []; this.state.voucher = ''; this.state.quote = null;
      this.saveCart();
      Modal.close('checkout-modal');
      this.showSuccess(order);
      this.renderCart();
      this.loadProducts(); this.loadFlash(); // cập nhật tồn kho
    } catch (e) {
      this.checkoutError(e.message);
      this.refreshCart(); // tồn kho/giá có thể đã đổi
    } finally { btn.disabled = false; btn.textContent = 'Đặt hàng'; }
  },

  checkoutError(msg) { const el = U.$('#co-error'); el.textContent = msg; el.classList.remove('hidden'); },

  showSuccess(o) {
    const q = o.quote;
    U.$('#success-body').innerHTML = `
    <div class="p-6 text-center">
      <div class="mx-auto mb-3 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
        <svg class="h-8 w-8" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg></div>
      <h2 class="text-lg font-bold">${o.status === 'pending_payment' ? 'Đã ghi nhận đơn hàng' : 'Đặt hàng thành công!'}</h2>
      <p class="text-xs text-zinc-500">Mã đơn: <b class="text-brand-600">${U.esc(o.order_id)}</b></p>
      <p class="mx-auto mt-2 max-w-xs text-xs text-zinc-600">${U.esc(o.message)}</p>
      <div class="my-4 space-y-2 rounded-xl border border-brand-100 bg-brand-50/50 p-4 text-left text-xs">
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Người nhận</span><b class="text-right">${U.esc(o.customer_name)} (${U.esc(o.customer_phone)})</b></div>
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Địa chỉ</span><span class="text-right font-medium">${U.esc(o.customer_address)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Thanh toán</span><b>${o.payment_method === 'cod' ? 'Khi nhận hàng (COD)' : 'Chuyển khoản'}</b></div>
        ${this.summaryRows(q)}
      </div>
      <button data-action="close-modal" data-target="success-modal" class="btn btn-primary w-full !py-3 uppercase">Tiếp tục mua sắm</button>
    </div>`;
    Modal.open('success-modal');
  },
};

/* ===================== Phòng Live AI ===================== */
const Live = {
  seeded: false,
  async open() {
    Modal.open('live-modal');
    if (this.seeded) return;
    this.seeded = true;
    const q = 'Shop ơi 1m62 52kg mặc size gì ạ?';
    this.add('Linh Đan 99', q);
    await this.ask('Linh Đan 99', q, 900);
  },
  add(user, text, host = false) {
    const box = U.$('#live-comments');
    const div = document.createElement('div');
    div.className = 'max-w-[88%] rounded-xl p-2 text-xs ' + (host ? 'border border-violet-400/40 bg-gradient-to-r from-violet-900/80 to-indigo-900/80 shadow-lg' : 'bg-black/60 backdrop-blur');
    const head = document.createElement('div');
    head.className = 'flex items-center gap-1.5 font-bold ' + (host ? 'text-amber-300' : 'text-zinc-300');
    if (host) { const b = document.createElement('span'); b.className = 'rounded bg-violet-600 px-1.5 text-[9px] uppercase text-white'; b.textContent = 'Host AI'; head.appendChild(b); }
    const name = document.createElement('span'); name.textContent = user + ':'; head.appendChild(name);
    const p = document.createElement('p'); p.className = 'mt-0.5 leading-relaxed'; p.textContent = text; // textContent: chống XSS
    div.append(head, p);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  },
  async ask(user, comment, delay = 600) {
    try {
      const d = await U.api('/api/ai/live-comment', { method: 'POST', body: { user_name: user, comment } });
      setTimeout(() => { this.add(d.host_name, d.reply, true); if (d.pinned_product) this.pin(d.pinned_product); }, delay);
    } catch (e) { this.add('Hệ thống', e.message, true); }
  },
  async send() {
    const input = U.$('#live-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    this.add('Bạn', text);
    await this.ask('bạn', text);
  },
  pin(p) {
    App.cache([p]);
    const box = U.$('#live-pinned');
    box.innerHTML = `
      <div class="flex items-center gap-3 rounded-2xl border-2 border-brand-600 bg-white p-2.5 text-zinc-900 shadow-2xl">
        ${U.img(p.images[0], p.name, 'h-14 w-12 flex-shrink-0 rounded-lg object-cover')}
        <div class="min-w-0 flex-1">
          <span class="flex items-center gap-1 text-[10px] font-extrabold uppercase text-brand-600"><span class="h-2 w-2 animate-ping rounded-full bg-red-600"></span>Đang ghim trên live</span>
          <h3 class="line-clamp-1 text-xs font-bold">${U.esc(p.name)}</h3>
          <span class="text-xs font-black text-brand-600">${U.vnd(p.final_price)}</span>
        </div>
        <button data-action="quickview" data-id="${p.id}" data-from="live-modal" class="btn btn-primary btn-sm">Chốt đơn</button>
      </div>`;
    box.classList.remove('hidden');
  },
  heart() {
    const panel = U.$('#live-modal > div');
    const h = document.createElement('span');
    h.className = 'floating-heart'; h.textContent = ['❤️', '💖', '💕'][Math.floor(Math.random() * 3)];
    h.style.right = (12 + Math.random() * 40) + 'px';
    panel.appendChild(h);
    setTimeout(() => h.remove(), 1900);
  },
};

/* ===================== Đăng ký hành động ===================== */
Object.assign(Actions, {
  'close-modal': d => Modal.close(d.target),
  'scroll-to': d => U.$('#' + d.target).scrollIntoView({ behavior: 'smooth' }),
  'set-category': d => { App.setCategory(d.cat); U.$('#catalog').scrollIntoView({ behavior: 'smooth' }); },
  'set-sort': d => { App.state.filters.sort = d.sort; App.renderSortButtons(); App.loadProducts(); },
  'reset-filters': () => App.resetFilters(),
  'hot-search': d => { U.$('#search-input').value = d.q; App.state.filters.search = d.q; App.loadProducts(); U.$('#catalog').scrollIntoView({ behavior: 'smooth' }); },
  'show-wishlist': () => {
    if (!App.state.wishlist.length) return U.toast('Bạn chưa lưu sản phẩm yêu thích nào');
    App.state.filters.wishlist = !App.state.filters.wishlist;
    App.renderProducts();
    U.$('#catalog').scrollIntoView({ behavior: 'smooth' });
  },
  wishlist: d => App.toggleWishlist(d.id),
  quickview: d => App.openQuickView(d.id, d.from),
  'qv-img': d => App.qvSetImage(Number(d.idx)),
  'qv-prev-img': () => App.qvSetImage((App.state.qv?.currentImgIdx ?? 0) - 1),
  'qv-next-img': () => App.qvSetImage((App.state.qv?.currentImgIdx ?? 0) + 1),
  'qv-size': d => App.qvSelect('size', d.size),
  'qv-color': d => App.qvSelect('color', d.color),
  'qv-qty': d => {
    const qv = App.state.qv;
    const max = Math.max(1, Math.min(CFG.maxQty, qv.product.stock));
    qv.qty = Math.max(1, Math.min(max, qv.qty + Number(d.delta)));
    U.$('#qv-qty').textContent = qv.qty;
  },
  'qv-add': () => App.qvAdd(false),
  'qv-buy': () => App.qvAdd(true),
  'open-cart': () => { Modal.open('cart-drawer'); App.refreshCart(); },
  'cart-qty': d => App.changeQty(Number(d.idx), Number(d.delta)),
  'cart-remove': d => App.removeLine(Number(d.idx)),
  checkout: () => App.openCheckout(),
  'apply-voucher': () => App.applyVoucher(),
  'use-voucher': d => { U.$('#co-voucher').value = d.code; App.applyVoucher(); },
  'claim-voucher': d => App.claimVoucher(d.code),
  video: d => App.openVideo(d.id),
  'open-live': () => Live.open(),
  'live-heart': () => Live.heart(),
});
document.addEventListener('change', e => { // đổi size/màu ngay trong giỏ
  const t = e.target;
  if (t.dataset && t.dataset.cartVariant) App.changeVariant(Number(t.dataset.idx), t.dataset.cartVariant, t.value);
});
document.addEventListener('keydown', e => { // thẻ video truy cập bằng bàn phím
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('.reel-card')) { e.preventDefault(); e.target.click(); }
});

document.addEventListener('DOMContentLoaded', () => App.init());
