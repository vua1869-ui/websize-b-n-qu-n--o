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
      const headers = body ? { 'Content-Type': 'application/json' } : {};
      const token = localStorage.getItem('aura_token');
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(url, {
        method, signal: ctrl.signal,
        headers,
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
    user: null,
    catalog: {}, categories: [], products: [], vouchers: [], videos: [], notice: '',
    savedVouchers: U.store.get('aura_vouchers_v2', []),
    cart: U.store.get('aura_cart_v2', []),
    wishlist: U.store.get('aura_wishlist', []),
    filters: { category: 'all', gender: 'all', sort: 'popular', search: '', min: null, max: null, wishlist: false },
    flash: { endsAt: 0, skew: 0, items: [], reloading: false },
    trends: [],
    trendingProducts: [],
    qv: { product: null, size: null, color: null, qty: 1 },
    quote: null, quoteError: '', voucher: '',
    usePoints: 0,
    loyalty: null,
    locations: null,
    _pollTimer: null,
  },
  _seq: { products: 0, quote: 0 },

  async init() {
    this.bindEvents();
    this.updateBadges();
    this.renderSortButtons();
    this.initAuth();
    await Promise.all([this.loadCategories(), this.loadLocations(), this.loadProducts(), this.loadTrending(), this.loadFlash(), this.loadVouchers(), this.loadVideos()]);
    this.sanitizeCart();
    this.refreshCart();
    ExitIntentUI.init();
    OmnichannelUI.init();

    // Tự động mở modal Tra cứu vận đơn nếu URL có query param ?tracking=...
    const trackingParam = new URLSearchParams(window.location.search).get('tracking');
    if (trackingParam && window.TrackingUI) {
      setTimeout(() => window.TrackingUI.openModal(trackingParam), 400);
    }
  },

  async initAuth() {
    try {
      const user = await U.api('/api/auth/me');
      if (user && user.id) {
        this.state.user = user;
        this.renderAuth(user);
      } else {
        this.state.user = null;
      }
    } catch {
      this.state.user = null;
      // chưa đăng nhập
    }
  },

  renderAuth(user) {
    const headerAuth = U.$('#header-auth-container');
    if (headerAuth) {
      headerAuth.innerHTML = `
        <div class="relative" id="user-menu-wrapper">
          <button id="user-menu-btn" class="flex items-center gap-2 rounded-md border border-white/20 bg-white/10 px-2.5 sm:px-3 py-1.5 text-xs font-medium text-white shadow-xs transition hover:bg-white/20">
            <div class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white text-[10px] font-bold text-stone-900">
              ${(user.full_name || user.username)[0].toUpperCase()}
            </div>
            <span class="hidden max-w-[100px] truncate sm:inline">${U.esc(user.full_name || user.username)}</span>
            <svg class="h-3.5 w-3.5 shrink-0 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
          </button>
          <div id="user-dropdown-menu" class="absolute right-0 top-full mt-1.5 w-52 rounded-md border border-stone-200 bg-white py-1 text-stone-800 shadow-lg hidden z-50">
            <div class="border-b border-stone-100 px-3.5 py-2.5">
              <p class="truncate text-xs font-semibold text-stone-900">${U.esc(user.full_name || user.name || user.username)}</p>
              <p class="truncate text-[10px] font-mono text-stone-400 mt-0.5">@${U.esc(user.username)} • ${user.role === 'admin' ? '<span class="text-stone-900 font-semibold">Admin</span>' : 'Thành viên'}</p>
            </div>
            ${user.role === 'admin' ? `
              <a href="/admin" class="flex items-center gap-2.5 px-3.5 py-2 text-xs font-medium text-stone-900 hover:bg-[#FAF9F6] transition">
                <svg class="h-4 w-4 shrink-0 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.75"><path stroke-linecap="round" stroke-linejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75"/></svg>
                <span>Quản trị Admin</span>
              </a>
            ` : ''}
            <a href="/profile" class="flex items-center gap-2.5 px-3.5 py-2 text-xs font-medium text-stone-700 hover:bg-[#FAF9F6] transition">
              <svg class="h-4 w-4 shrink-0 text-stone-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z"/></svg>
              <span>Tài khoản của tôi</span>
            </a>
            <button data-action="auth-logout" class="flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-xs font-medium text-stone-500 hover:text-rose-600 hover:bg-[#FAF9F6] border-t border-stone-100 transition">
              <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9"/></svg>
              <span>Đăng xuất</span>
            </button>
          </div>
        </div>
      `;

      const btn = U.$('#user-menu-btn');
      const dropdown = U.$('#user-dropdown-menu');
      if (btn && dropdown) {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          dropdown.classList.toggle('hidden');
        });
        document.addEventListener('click', (e) => {
          if (!e.target.closest('#user-menu-wrapper')) {
            dropdown.classList.add('hidden');
          }
        });
      }
    }

    const topAuth = U.$('#topbar-auth');
    if (topAuth) {
      topAuth.innerHTML = `
        <span class="text-zinc-300">Xin chào, <strong class="text-white">${U.esc(user.full_name || user.username)}</strong></span>
        ${user.role === 'admin' ? '<span class="text-zinc-600">•</span><a href="/admin" class="font-bold text-amber-400 hover:underline">Quản trị</a>' : ''}
        <span class="text-zinc-600">•</span>
        <a href="/profile" class="hover:text-white">Tài khoản</a>
        <span class="text-zinc-600">•</span>
        <button data-action="auth-logout" class="hover:text-rose-300">Đăng xuất</button>
      `;
    }
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

  /* ---------- Trending (AI Fashion Trend Detection & Recommendation) ---------- */
  async loadTrending(forceRefresh = false) {
    const chipsEl = U.$('#trend-chips');
    const prodsGrid = U.$('#trending-products-grid');
    if (!chipsEl || !prodsGrid) return;

    try {
      // Thu thập sở thích ẩn danh từ localStorage cho Personalization
      const behavior = U.store.get('aura_user_behavior', { viewed_cats: {}, liked_styles: {}, searches: [] }) || {};
      const viewedCats = (behavior && behavior.viewed_cats) || {};
      const likedStyles = (behavior && behavior.liked_styles) || {};
      const topCats = Object.entries(viewedCats)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(x => x[0])
        .join(',');
      const topStyles = Object.entries(likedStyles)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(x => x[0])
        .join(',');

      const trendsUrl = '/api/trends?limit=10';
      const prodsUrl = `/api/trending-products?limit=8${topCats ? `&user_cats=${encodeURIComponent(topCats)}` : ''}${topStyles ? `&user_styles=${encodeURIComponent(topStyles)}` : ''}`;

      const [trends, trendingData] = await Promise.all([
        U.api(trendsUrl),
        U.api(prodsUrl),
      ]);

      this.state.trends = trends || [];
      this.state.trendingProducts = trendingData || [];

      if (trendingData && trendingData.length) {
        this.cache(trendingData.map(item => item.product));
      }

      this.renderTrending();
    } catch (err) {
      console.warn('[Trending] Lỗi khi nạp xu hướng:', err);
      if (chipsEl) chipsEl.innerHTML = '<span class="text-xs text-zinc-400">Xu hướng tạm thời chưa cập nhật.</span>';
      if (prodsGrid) prodsGrid.innerHTML = '';
    }
  },

  renderTrending() {
    const chipsEl = U.$('#trend-chips');
    const prodsGrid = U.$('#trending-products-grid');
    const badgeEl = U.$('#trend-data-badge');
    const timeEl = U.$('#trend-updated-at');
    const trends = this.state.trends || [];
    const trendingProds = this.state.trendingProducts || [];

    if (!trends.length && !trendingProds.length) {
      const section = U.$('#trending-section');
      if (section) section.classList.add('hidden');
      return;
    }

    // Hiển thị nguồn dữ liệu minh bạch
    const firstTrend = trends[0];
    if (firstTrend && badgeEl) {
      if (firstTrend.source === 'google_trends') {
        badgeEl.textContent = 'Google Trends VN';
        badgeEl.className = 'rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700 border border-emerald-200';
      } else if (firstTrend.source === 'cached') {
        badgeEl.textContent = 'Dữ liệu xu hướng AURA';
        badgeEl.className = 'rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700 border border-blue-200';
      } else {
        badgeEl.textContent = 'Demo Trends';
        badgeEl.className = 'rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700 border border-amber-200';
      }
    }

    if (firstTrend && timeEl) {
      timeEl.textContent = `Cập nhật lúc ${firstTrend.updated_at ? firstTrend.updated_at.substring(11, 16) : 'hôm nay'}`;
    }

    // Render Trend Chips
    if (chipsEl && trends.length) {
      chipsEl.innerHTML = trends.map(t => {
        const isRising = t.status === 'rising';
        const growthBadge = isRising
          ? `<span class="text-rose-600 font-extrabold text-[11px]">↑ +${t.growth_rate}%</span>`
          : (t.status === 'declining' ? `<span class="text-zinc-400 font-semibold text-[11px]">↓ ${t.growth_rate}%</span>` : `<span class="text-amber-600 font-semibold text-[11px]">→ ổn định</span>`);
        return `
          <button type="button" data-action="click-trend" data-kw="${U.esc(t.keyword)}"
                  class="group flex flex-shrink-0 items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1 text-xs font-bold text-zinc-800 shadow-sm transition hover:border-amber-400 hover:bg-amber-50/50 hover:shadow">
            <span>🔥</span>
            <span>${U.esc(t.keyword)}</span>
            ${growthBadge}
          </button>`;
      }).join('');
    }

    // Render Trending Products
    if (prodsGrid && trendingProds.length) {
      prodsGrid.innerHTML = trendingProds.map(item => this.trendingCard(item)).join('');
    }
  },

  trendingCard(item) {
    const p = item.product;
    const rank = item.rank;
    const isTop1 = rank === 1;
    const out = !p.in_stock || p.stock <= 0;
    const sold = p.sold_count >= 1000 ? (p.sold_count / 1000).toFixed(1) + 'k' : p.sold_count;

    const rankBadge = isTop1
      ? `<span class="badge badge-top-trend animate-pulse">🔥 #1 TREND</span>`
      : `<span class="badge badge-hot-trend">🔥 TOP ${rank} TREND</span>`;

    return `
    <article class="product-card group relative flex flex-col justify-between overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm transition hover:shadow-lg hover:border-amber-400/60" data-id="${U.esc(p.id)}">
      <div class="cursor-pointer" data-action="quickview" data-id="${U.esc(p.id)}">
        <div class="relative aspect-square w-full overflow-hidden bg-zinc-100">
          ${U.img(p.images[0], p.name, 'h-full w-full object-cover transition duration-300 group-hover:scale-105')}
          <div class="absolute left-2 top-2 flex flex-col gap-1 items-start">
            ${rankBadge}
            <span class="rounded bg-black/60 px-1.5 py-0.5 text-[9px] font-bold text-white backdrop-blur-xs">Score ${Math.round(item.final_score)}</span>
          </div>
          ${p.discount_percent > 0 ? `<span class="badge badge-sale absolute right-2 top-2">-${p.discount_percent}%</span>` : ''}
        </div>
        <div class="p-3">
          <div class="mb-1 flex items-center justify-between text-[11px] text-zinc-400">
            <span>${U.esc(p.category_name)}</span>
            <span class="text-emerald-700 font-semibold">Còn ${p.stock}</span>
          </div>
          <h3 class="line-clamp-2 text-xs font-bold text-zinc-800 transition group-hover:text-brand-600 sm:text-sm" title="${U.esc(p.name)}">${U.esc(p.name)}</h3>
          
          <div class="mt-1.5 flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
            <span class="text-amber-500">⚡</span>
            <span class="truncate">${U.esc(item.reason)}</span>
          </div>

          <div class="mt-2.5">
            <div class="flex items-baseline gap-1.5">
              <span class="text-sm font-extrabold leading-none text-brand-600 sm:text-base">${U.vnd(p.final_price)}</span>
              ${p.discount_percent > 0 ? `<span class="text-[10px] text-zinc-400 line-through">${U.vnd(p.original_price)}</span>` : ''}
            </div>
            <div class="mt-2 flex items-center justify-between border-t border-zinc-100 pt-1.5 text-[11px] text-zinc-500">
              <span><span class="text-amber-400">★</span> <b class="text-zinc-700">${p.rating}</b> • Đã bán ${sold}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="flex gap-1.5 px-2.5 pb-2.5">
        <button data-action="consult" data-id="${U.esc(p.id)}" class="btn btn-ai-soft btn-sm flex-1" title="Tư vấn phối đồ cùng AI">✨ AI Stylist</button>
        <button data-action="quickview" data-id="${U.esc(p.id)}" ${out ? 'disabled' : ''} class="btn btn-primary btn-sm">Mua</button>
      </div>
    </article>`;
  },

  /* ---------- Xem nhanh sản phẩm ---------- */
  /* ---------- Xem nhanh sản phẩm ---------- */
  async openQuickView(id, from) {
    const p = this.state.catalog[id];
    if (!p) return;
    if (from) Modal.close(from);

    // Ghi nhận hành vi xem sản phẩm ẩn danh
    try {
      const beh = U.store.get('aura_user_behavior', { viewed_cats: {}, liked_styles: {}, searches: [] });
      if (p.category) beh.viewed_cats[p.category] = (beh.viewed_cats[p.category] || 0) + 1;
      if (p.style) beh.liked_styles[p.style] = (beh.liked_styles[p.style] || 0) + 1;
      U.store.set('aura_user_behavior', beh);
    } catch { /* ignore */ }

    // Nạp ma trận biến thể Màu x Size nếu chưa có
    if (!p.variants || !p.variants.length) {
      try {
        const variants = await U.api(`/api/products/${encodeURIComponent(p.id)}/variants`);
        if (variants && variants.length) p.variants = variants;
      } catch { /* fallback */ }
    }

    const defaultColor = (p.colors && p.colors[0] && p.colors[0].name) || '';
    this.state.qv = { product: p, size: null, color: defaultColor, qty: 1, currentImgIdx: 0 };
    const out = !p.in_stock || p.stock <= 0;

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
            <div class="mb-1.5 text-xs font-semibold text-zinc-700">Màu sắc: <span id="qv-color-label" class="font-bold text-brand-600">${U.esc(defaultColor)}</span></div>
            <div class="flex flex-wrap gap-2" id="qv-colors">
              ${p.colors.map((c, i) => `<button data-action="qv-color" data-color="${U.esc(c.name)}" title="${U.esc(c.name)}" aria-label="${U.esc(c.name)}" aria-pressed="${i === 0}"
                class="h-7 w-7 rounded-full border-2 transition ${i === 0 ? 'border-brand-600 ring-2 ring-brand-200' : 'border-zinc-200'}" style="background-color:${U.esc(c.hex)}"></button>`).join('')}
            </div>
          </div>

          <div class="mt-4">
            <div class="mb-1.5 flex items-center justify-between">
              <span class="text-xs font-semibold text-zinc-700">Kích cỡ: <span id="qv-size-label" class="font-bold text-brand-600"></span></span>
              <div class="flex items-center gap-2">
                <button type="button" data-action="open-size-chart" data-product-id="${p.id}" class="text-xs font-bold text-brand-600 hover:underline flex items-center gap-1">📐 Bảng số đo</button>
                <span class="text-zinc-300">•</span>
                <button type="button" data-action="open-size" data-product-id="${p.id}" class="text-xs font-bold text-violet-700 hover:underline flex items-center gap-1">✨ AI tính size</button>
              </div>
            </div>
            <div class="flex flex-wrap gap-2" id="qv-sizes">
              <!-- Render động theo màu được chọn -->
            </div>
            <p id="qv-size-hint" class="mt-1.5 hidden text-xs font-semibold text-red-600">Vui lòng chọn size trước khi thêm vào giỏ.</p>
          </div>
        </div>

        <div class="mt-6 space-y-2 border-t border-zinc-100 pt-4">
          <div id="qv-stock-status" class="text-xs font-medium"></div>
          <div class="flex gap-2">
            <div class="flex items-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-50">
              <button data-action="qv-qty" data-delta="-1" class="px-3 py-1.5 font-bold text-zinc-600 hover:bg-zinc-100" aria-label="Giảm">−</button>
              <span id="qv-qty" class="min-w-6 text-center text-xs font-bold">1</span>
              <button data-action="qv-qty" data-delta="1" class="px-3 py-1.5 font-bold text-zinc-600 hover:bg-zinc-100" aria-label="Tăng">+</button>
            </div>
            <button id="qv-btn-add" data-action="qv-add" ${out ? 'disabled' : ''} class="btn btn-soft flex-1">🛒 Thêm vào giỏ</button>
            <button id="qv-btn-buy" data-action="qv-buy" ${out ? 'disabled' : ''} class="btn btn-primary flex-1">Mua ngay</button>
          </div>
          <button data-action="open-outfit" data-product-id="${p.id}" class="btn btn-ai-soft w-full">✨ AI phối trọn bộ cùng món này (giảm ${CFG.combo}%)</button>
        </div>
      </div>
    </div>
    
    <!-- Khu vực Đánh giá & Bằng chứng Xã hội (Phase 2) -->
    <div class="border-t border-zinc-200 bg-zinc-50/70 p-5 sm:p-6" id="qv-reviews-container">
      <div class="py-6 text-center text-xs text-zinc-400">Đang nạp đánh giá từ người mua...</div>
    </div>`;

    this.qvRenderSizesForColor(defaultColor);
    Modal.open('quickview-modal');
    this.loadProductReviews(p.id);
  },

  qvRenderSizesForColor(colorName) {
    const qv = this.state.qv;
    const p = qv.product;
    if (!p) return;

    const sizesContainer = U.$('#qv-sizes');
    if (!sizesContainer) return;

    const variants = p.variants || [];
    const sizes = p.sizes || [];

    let availableSizes = [];
    const sizeData = sizes.map(s => {
      const v = variants.find(x => x.color === colorName && x.size === s);
      const stock = v ? v.stock : p.stock;
      const isSoldOut = stock <= 0;
      if (!isSoldOut) availableSizes.push({ size: s, stock });
      return { size: s, stock, isSoldOut };
    });

    if (!qv.size || sizeData.some(d => d.size === qv.size && d.isSoldOut)) {
      qv.size = availableSizes.length > 0 ? availableSizes[0].size : null;
    }

    sizesContainer.innerHTML = sizeData.map(d => {
      const isSelected = qv.size === d.size && !d.isSoldOut;
      if (d.isSoldOut) {
        return `<button type="button" disabled title="Phân loại ${U.esc(colorName)} - Size ${U.esc(d.size)} đã hết hàng"
          class="btn-size-disabled rounded border px-3 py-1 text-xs font-semibold select-none">${U.esc(d.size)} (Hết)</button>`;
      }
      return `<button type="button" data-action="qv-size" data-size="${U.esc(d.size)}" data-stock="${d.stock}" aria-pressed="${isSelected}"
        class="rounded border px-3 py-1 text-xs font-semibold transition ${isSelected ? 'border-brand-600 bg-brand-600 text-white shadow-xs' : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400'}">${U.esc(d.size)}</button>`;
    }).join('');

    this.qvUpdateStockDisplay();
  },

  qvUpdateStockDisplay() {
    const qv = this.state.qv;
    const p = qv.product;
    if (!p) return;

    const sizeLabel = U.$('#qv-size-label');
    const stockEl = U.$('#qv-stock-status');
    const btnAdd = U.$('#qv-btn-add');
    const btnBuy = U.$('#qv-btn-buy');
    const hint = U.$('#qv-size-hint');

    if (hint) hint.classList.add('hidden');
    if (sizeLabel) sizeLabel.textContent = qv.size || '(Chưa chọn)';

    if (!qv.size) {
      if (stockEl) stockEl.innerHTML = '<span class="text-rose-600 font-semibold">Vui lòng chọn Kích cỡ còn hàng</span>';
      if (btnAdd) btnAdd.disabled = true;
      if (btnBuy) btnBuy.disabled = true;
      return;
    }

    const variants = p.variants || [];
    const v = variants.find(x => x.color === qv.color && x.size === qv.size);
    const stock = v ? v.stock : p.stock;

    if (stock <= 0) {
      if (stockEl) stockEl.innerHTML = `<span class="text-rose-600 font-bold">⚠️ Phân loại Màu ${U.esc(qv.color)} - Size ${U.esc(qv.size)} đã tạm hết hàng</span>`;
      if (btnAdd) btnAdd.disabled = true;
      if (btnBuy) btnBuy.disabled = true;
    } else if (stock <= 5) {
      if (stockEl) stockEl.innerHTML = `<span class="text-amber-700 font-bold">⚡ Chỉ còn ${stock} sản phẩm cho phân loại Màu ${U.esc(qv.color)} - Size ${U.esc(qv.size)}!</span>`;
      if (btnAdd) btnAdd.disabled = false;
      if (btnBuy) btnBuy.disabled = false;
    } else {
      if (stockEl) stockEl.innerHTML = `<span class="text-emerald-700 font-medium">✓ Còn ${stock} sản phẩm sẵn sàng giao</span>`;
      if (btnAdd) btnAdd.disabled = false;
      if (btnBuy) btnBuy.disabled = false;
    }
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
    if (kind === 'color') {
      qv.color = value;
      const colorLabel = U.$('#qv-color-label');
      if (colorLabel) colorLabel.textContent = value;
      U.$$('#qv-colors button').forEach(b => {
        const on = b.dataset.color === value;
        b.setAttribute('aria-pressed', on);
        b.className = 'h-7 w-7 rounded-full border-2 transition ' + (on ? 'border-brand-600 ring-2 ring-brand-200' : 'border-zinc-200');
      });
      this.qvRenderSizesForColor(value);
    } else if (kind === 'size') {
      qv.size = value;
      U.$$('#qv-sizes button[data-action="qv-size"]').forEach(b => {
        const on = b.dataset.size === value;
        b.setAttribute('aria-pressed', on);
        b.className = 'rounded border px-3 py-1 text-xs font-semibold transition ' + (on ? 'border-brand-600 bg-brand-600 text-white shadow-xs' : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400');
      });
      this.qvUpdateStockDisplay();
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

  /* ---------- Đánh giá & Bằng chứng Xã hội (Phase 2) ---------- */
  async loadProductReviews(productId, ratingFilter = null) {
    const container = U.$('#qv-reviews-container');
    if (!container) return;

    try {
      const url = `/api/products/${encodeURIComponent(productId)}/reviews${ratingFilter ? `?rating=${ratingFilter}` : ''}`;
      const data = await U.api(url);
      const summary = data.summary || {};
      const reviews = data.reviews || [];
      const breakdown = summary.rating_breakdown || { 5: 0, 4: 0, 3: 0, 2: 0, 1: 0 };
      const totalRev = summary.total_reviews || 0;
      const isLogged = !!this.state.user;

      container.innerHTML = `
        <div class="space-y-5">
          <div class="flex items-center justify-between border-b border-zinc-200 pb-3">
            <div>
              <h3 class="text-sm font-bold uppercase tracking-wider text-zinc-900 flex items-center gap-1.5">
                <span>⭐</span> Đánh giá từ khách hàng đã mua
              </h3>
              <p class="text-[11px] text-zinc-500">Người thật • Số đo thật • Trải nghiệm chuẩn</p>
            </div>
            <button type="button" onclick="App.toggleReviewForm()" class="btn btn-primary btn-sm !py-1.5 text-xs font-semibold">
              ✍️ Viết đánh giá
            </button>
          </div>

          <!-- Tóm tắt số sao & Phân bổ -->
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 rounded-xl border border-zinc-200 bg-white p-4">
            <div class="flex flex-col items-center justify-center border-b sm:border-b-0 sm:border-r border-zinc-100 pb-3 sm:pb-0">
              <span class="text-3xl font-black text-brand-600">${summary.average_rating || 5.0}</span>
              <div class="star-rating text-sm my-1">
                ${'★'.repeat(Math.round(summary.average_rating || 5))}${'☆'.repeat(5 - Math.round(summary.average_rating || 5))}
              </div>
              <span class="text-[11px] text-zinc-500 font-medium">${totalRev} lượt đánh giá thực tế</span>
            </div>

            <div class="space-y-1.5 col-span-2">
              ${[5, 4, 3, 2, 1].map(star => {
                const count = breakdown[star] || 0;
                const pct = totalRev > 0 ? Math.round((count / totalRev) * 100) : 0;
                return `
                  <div class="flex items-center gap-2 text-xs">
                    <span class="w-10 text-[11px] font-semibold text-zinc-600">${star} sao</span>
                    <div class="flex-1 h-2 rounded-full bg-zinc-100 overflow-hidden">
                      <div class="h-full bg-amber-400 rounded-full" style="width: ${pct}%"></div>
                    </div>
                    <span class="w-8 text-right text-[11px] text-zinc-400 font-mono">${count}</span>
                  </div>
                `;
              }).join('')}
              <div class="pt-1 text-[11px] font-medium text-emerald-700 flex items-center gap-1">
                <span>✓</span> ${summary.fit_feedback_summary || '96% khách hàng đánh giá đúng kích cỡ'}
              </div>
            </div>
          </div>

          <!-- Bộ lọc số sao -->
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <span class="text-zinc-500 font-medium">Lọc theo:</span>
            <button type="button" onclick="App.loadProductReviews('${productId}', null)"
              class="px-2.5 py-1 rounded-full text-[11px] font-semibold transition ${!ratingFilter ? 'bg-zinc-900 text-white' : 'bg-white border border-zinc-200 text-zinc-700 hover:border-zinc-400'}">
              Tất cả (${totalRev})
            </button>
            ${[5, 4, 3].map(st => `
              <button type="button" onclick="App.loadProductReviews('${productId}', ${st})"
                class="px-2.5 py-1 rounded-full text-[11px] font-semibold transition ${ratingFilter === st ? 'bg-zinc-900 text-white' : 'bg-white border border-zinc-200 text-zinc-700 hover:border-zinc-400'}">
                ${st} sao (${breakdown[st] || 0})
              </button>
            `).join('')}
          </div>

          <!-- Form viết đánh giá mới (Mặc định ẩn) -->
          <div id="qv-review-form-box" class="hidden rounded-xl border border-brand-200 bg-brand-50/40 p-4 space-y-3">
            <h4 class="text-xs font-bold text-zinc-900 uppercase">Gửi đánh giá của bạn</h4>
            ${!isLogged ? `
              <div class="rounded-xl border border-amber-200 bg-amber-50/90 p-4 text-center space-y-2">
                <div class="flex items-center justify-center gap-1.5 text-xs font-bold text-amber-800">
                  <span>🔒</span> Vui lòng đăng nhập để đánh giá
                </div>
                <p class="text-xs text-amber-700">Chỉ những khách hàng đã mua sản phẩm tại AURA STUDIO mới có thể gửi đánh giá và nhận xét.</p>
                <div class="pt-1.5 flex justify-center gap-2">
                  <a href="/login" class="btn btn-primary text-xs !py-1.5 px-4 font-bold">Đăng nhập ngay</a>
                  <button type="button" onclick="App.toggleReviewForm()" class="btn btn-soft text-xs !py-1.5 px-3">Đóng</button>
                </div>
              </div>
            ` : `
              <form onsubmit="event.preventDefault(); App.submitProductReview('${productId}');" class="space-y-3 text-xs">
                <div>
                  <label class="block font-semibold text-zinc-700 mb-1">Mức độ hài lòng của bạn *</label>
                  <div class="star-rating star-rating-interactive text-xl text-amber-400" id="review-stars-input">
                    <button type="button" onclick="App.setReviewStar(1)">★</button>
                    <button type="button" onclick="App.setReviewStar(2)">★</button>
                    <button type="button" onclick="App.setReviewStar(3)">★</button>
                    <button type="button" onclick="App.setReviewStar(4)">★</button>
                    <button type="button" onclick="App.setReviewStar(5)">★</button>
                  </div>
                  <input type="hidden" id="rf-rating" value="5" />
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Họ tên của bạn *</label>
                    <input type="text" id="rf-name" required value="${U.esc((this.state.user && (this.state.user.full_name || this.state.user.username)) || '')}" placeholder="VD: Nguyễn Thảo Ly" class="field !py-2 text-xs" />
                  </div>
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Cảm nhận độ vừa vặn *</label>
                    <select id="rf-fit" class="field !py-2 text-xs">
                      <option value="Vừa vặn">Vừa vặn hoàn hảo</option>
                      <option value="Hơi rộng">Hơi rộng một chút</option>
                      <option value="Hơi chật">Hơi chật một chút</option>
                      <option value="Rộng">Rộng hơn mong đợi</option>
                      <option value="Chật">Chật hơn mong đợi</option>
                    </select>
                  </div>
                </div>

                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Chiều cao (cm)</label>
                    <input type="number" id="rf-height" min="100" max="220" placeholder="162" class="field !py-2 text-xs" />
                  </div>
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Cân nặng (kg)</label>
                    <input type="number" id="rf-weight" min="30" max="180" placeholder="48" class="field !py-2 text-xs" />
                  </div>
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Size đã mua</label>
                    <input type="text" id="rf-size" placeholder="S" class="field !py-2 text-xs uppercase" />
                  </div>
                  <div>
                    <label class="block font-semibold text-zinc-700 mb-1">Màu đã mua</label>
                    <input type="text" id="rf-color" placeholder="Be / Kem" class="field !py-2 text-xs" />
                  </div>
                </div>

                <div>
                  <label class="block font-semibold text-zinc-700 mb-1">Nhận xét chi tiết (chất vải, đường may, form dáng...) *</label>
                  <textarea id="rf-comment" rows="2" required placeholder="Chia sẻ trải nghiệm thực tế để giúp mọi người dễ dàng chọn size nhé..." class="field !py-2 text-xs"></textarea>
                </div>

                <div class="flex justify-end gap-2 pt-1">
                  <button type="button" onclick="App.toggleReviewForm()" class="btn btn-soft text-xs !py-1.5">Hủy</button>
                  <button type="submit" id="rf-submit-btn" class="btn btn-primary text-xs !py-1.5 font-bold uppercase">Gửi đánh giá</button>
                </div>
              </form>
            `}
          </div>

          <!-- Danh sách bài đánh giá -->
          <div class="space-y-3" id="qv-reviews-list">
            ${reviews.length === 0 ? `
              <div class="text-center py-6 text-xs text-zinc-400">Chưa có đánh giá nào cho phân loại này. Hãy là người đầu tiên nhận xét!</div>
            ` : reviews.map(r => `
              <div class="rounded-xl border border-zinc-100 bg-white p-3.5 space-y-2 text-xs shadow-2xs">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <div class="h-7 w-7 rounded-full bg-zinc-200 flex items-center justify-center font-bold text-zinc-700 text-[11px]">
                      ${U.esc(r.user_name ? r.user_name[0].toUpperCase() : 'K')}
                    </div>
                    <div>
                      <div class="flex items-center gap-1.5">
                        <strong class="text-zinc-900 font-semibold">${U.esc(r.user_name)}</strong>
                        ${(r.is_verified_buyer === true || r.is_verified_buyer === 1) ? '<span class="verified-buyer-badge">✓ Đã mua hàng</span>' : ''}
                      </div>
                      <div class="star-rating text-[11px] mt-0.5">
                        ${'★'.repeat(r.rating)}${'☆'.repeat(5 - r.rating)}
                      </div>
                    </div>
                  </div>
                  <span class="text-[10px] text-zinc-400 font-mono">${U.esc(r.created_at || '')}</span>
                </div>

                <!-- Tag số đo người mua -->
                <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-600 bg-zinc-50 rounded-lg p-2 border border-zinc-100">
                  ${r.height_cm ? `<span>Cao <strong>${r.height_cm}cm</strong></span>` : ''}
                  ${r.height_cm && r.weight_kg ? `<span>•</span>` : ''}
                  ${r.weight_kg ? `<span>Nặng <strong>${r.weight_kg}kg</strong></span>` : ''}
                  ${r.purchased_size ? `<span>• Size: <strong class="text-brand-600">${U.esc(r.purchased_size)}</strong></span>` : ''}
                  ${r.purchased_color ? `<span>(${U.esc(r.purchased_color)})</span>` : ''}
                  ${r.fit_feedback ? `<span class="fit-badge ml-auto">${U.esc(r.fit_feedback)}</span>` : ''}
                </div>

                <p class="text-zinc-700 leading-relaxed text-xs">${U.esc(r.comment)}</p>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    } catch (e) {
      container.innerHTML = `<div class="py-6 text-center text-xs text-red-500">Không thể tải đánh giá: ${U.esc(e.message)}</div>`;
    }
  },

  toggleReviewForm() {
    const box = U.$('#qv-review-form-box');
    if (box) box.classList.toggle('hidden');
  },

  setReviewStar(n) {
    const hidden = U.$('#rf-rating');
    if (hidden) hidden.value = n;
    const container = U.$('#review-stars-input');
    if (container) {
      const btns = container.querySelectorAll('button');
      btns.forEach((b, i) => {
        b.textContent = i < n ? '★' : '☆';
        b.style.color = i < n ? '#fbbf24' : '#d4d4d8';
      });
    }
  },

  async submitProductReview(productId) {
    if (!this.state.user) {
      U.toast('Vui lòng đăng nhập trước khi gửi đánh giá.', 'error');
      return;
    }

    const rating = Number(U.$('#rf-rating').value) || 5;
    const name = U.$('#rf-name').value.trim();
    const comment = U.$('#rf-comment').value.trim();
    const fit = U.$('#rf-fit').value;
    const height = parseFloat(U.$('#rf-height').value) || null;
    const weight = parseFloat(U.$('#rf-weight').value) || null;
    const size = U.$('#rf-size').value.trim() || null;
    const color = U.$('#rf-color').value.trim() || null;

    const submitBtn = U.$('#rf-submit-btn');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Đang gửi...'; }

    try {
      await U.api(`/api/products/${encodeURIComponent(productId)}/reviews`, {
        method: 'POST',
        body: {
          user_name: name,
          rating: rating,
          comment: comment,
          fit_feedback: fit,
          height_cm: height,
          weight_kg: weight,
          purchased_size: size,
          purchased_color: color
        }
      });
      U.toast('Đánh giá của bạn đã được đăng thành công!', 'ok');
      await this.loadProductReviews(productId);
    } catch (e) {
      U.toast(e.message || 'Không thể gửi đánh giá', 'error');
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Gửi đánh giá'; }
    }
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
      const q = await U.api('/api/orders/quote', {
        method: 'POST',
        body: {
          items: this.cartPayload(),
          voucher_code: this.state.voucher || null,
          use_points: this.state.usePoints || 0
        }
      });
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
    if (after >= CFG.freeShip || (this.state.loyalty && this.state.loyalty.free_shipping_all_orders)) {
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
    if (q.points_discount) h += row(`Điểm thưởng AURA (${q.points_used} điểm)`, '−' + U.vnd(q.points_discount), 'font-semibold text-amber-700');
    const ship = q.shipping_fee - q.shipping_discount;
    h += row('Phí vận chuyển', ship ? U.vnd(ship) : 'Miễn phí', ship ? 'text-zinc-600' : 'font-semibold text-emerald-700');
    h += row('Tổng cộng', U.vnd(q.total), 'border-t border-zinc-200 pt-2 text-sm font-extrabold text-brand-600');
    if (q.points_earned) {
      h += `<div class="mt-2 text-[11px] font-semibold text-amber-800 bg-amber-50 rounded-lg p-2 flex items-center justify-between border border-amber-200/60">
        <span class="flex items-center gap-1"><span>💎</span> Tích lũy sau đơn này:</span>
        <span class="font-bold text-amber-900">+${q.points_earned} điểm (Hạng thẻ VIP)</span>
      </div>`;
    }
    return h;
  },

  /* ---------- Địa giới hành chính 2 cấp (Tỉnh/Thành -> Xã/Phường/Đặc khu) ---------- */
  async loadLocations() {
    if (this.state.locations && this.state.locations.length) return this.state.locations;
    try {
      this.state.locations = await U.api('/api/locations');
    } catch (e) {
      console.warn('Lỗi tải danh mục địa giới hành chính:', e);
      this.state.locations = [];
    }
    return this.state.locations;
  },

  async initLocations() {
    const provSelect = U.$('#co-province');
    const wardSelect = U.$('#co-ward');
    const streetInput = U.$('#co-street');
    if (!provSelect || !wardSelect) return;

    await this.loadLocations();
    const locs = this.state.locations || [];

    // Nạp danh sách 34 tỉnh/thành
    provSelect.innerHTML = '<option value="">-- Chọn Tỉnh / Thành phố --</option>' +
      locs.map(p => `<option value="${p.code}" data-name="${U.esc(p.name)}">${U.esc(p.name)}</option>`).join('');

    // Khôi phục lựa chọn cũ nếu có trong localStorage aura_customer
    const me = U.store.get('aura_customer', {});
    const savedProvCode = Number(me.province_code);
    const savedWardCode = Number(me.ward_code);

    let matchedProv = null;
    if (savedProvCode) {
      matchedProv = locs.find(p => p.code === savedProvCode);
    }
    if (!matchedProv && me.province) {
      matchedProv = locs.find(p => p.name === me.province || p.name.includes(me.province));
    }

    if (matchedProv) {
      provSelect.value = String(matchedProv.code);
      this.renderWards(matchedProv.code, savedWardCode || me.ward);
    } else {
      provSelect.value = '';
      wardSelect.innerHTML = '<option value="">-- Chọn Xã / Phường / Đặc khu --</option>';
      wardSelect.disabled = true;
    }

    if (!provSelect._bound) {
      provSelect._bound = true;
      provSelect.addEventListener('change', () => {
        const pCode = Number(provSelect.value);
        this.renderWards(pCode);
        this.syncAddress();
      });
    }

    if (!wardSelect._bound) {
      wardSelect._bound = true;
      wardSelect.addEventListener('change', () => this.syncAddress());
    }

    if (streetInput && !streetInput._bound) {
      streetInput._bound = true;
      streetInput.addEventListener('input', () => this.syncAddress());
    }

    this.syncAddress();
  },

  renderWards(provCode, preselectedWard) {
    const wardSelect = U.$('#co-ward');
    if (!wardSelect) return;

    const locs = this.state.locations || [];
    const prov = locs.find(p => p.code === Number(provCode));

    if (!prov || !prov.wards || !prov.wards.length) {
      wardSelect.innerHTML = '<option value="">-- Chọn Xã / Phường / Đặc khu --</option>';
      wardSelect.disabled = true;
      this.syncAddress();
      return;
    }

    // Client-side render wards: lọc mảng wards tương ứng từ province đã nạp
    wardSelect.innerHTML = '<option value="">-- Chọn Xã / Phường / Đặc khu --</option>' +
      prov.wards.map(w => `<option value="${w.code}" data-name="${U.esc(w.name)}">${U.esc(w.name)}</option>`).join('');
    wardSelect.disabled = false;

    if (preselectedWard != null) {
      const match = prov.wards.find(w => w.code === Number(preselectedWard) || w.name === preselectedWard);
      if (match) {
        wardSelect.value = String(match.code);
      } else {
        wardSelect.value = '';
      }
    } else {
      wardSelect.value = '';
    }

    this.syncAddress();
  },

  syncAddress() {
    const provSelect = U.$('#co-province');
    const wardSelect = U.$('#co-ward');
    const streetInput = U.$('#co-street');
    const addrHidden = U.$('#co-address');

    const provName = provSelect?.selectedOptions[0]?.dataset?.name || '';
    const wardName = wardSelect?.selectedOptions[0]?.dataset?.name || '';
    const street = streetInput ? streetInput.value.trim() : '';

    const parts = [street, wardName, provName].filter(Boolean);
    if (addrHidden) addrHidden.value = parts.join(', ');
  },

  /* ---------- Thanh toán ---------- */
  async openCheckout() {
    if (!this.state.cart.length) { U.toast('Giỏ hàng đang trống', 'error'); return; }
    if (this.state.quoteError) { U.toast(this.state.quoteError, 'error'); return; }
    Modal.close('cart-drawer');
    const me = U.store.get('aura_customer', {});
    U.$('#co-name').value ||= me.name || '';
    U.$('#co-phone').value ||= me.phone || '';
    if (U.$('#co-street') && !U.$('#co-street').value) {
      U.$('#co-street').value = me.specific_address || '';
    }
    U.$('#co-error').classList.add('hidden');
    U.$('#co-voucher').value = this.state.voucher || '';
    this.renderSavedVoucherChips();
    await Promise.all([this.initLocations(), this.initLoyaltyCheckout()]);
    this.renderCheckoutSummary();
    Modal.open('checkout-modal');
    if (!this.state.voucher && this.state.savedVouchers.length) await this.autoPickVoucher();
  },

  async initLoyaltyCheckout() {
    const container = U.$('#co-loyalty-container');
    if (!container) return;
    try {
      const status = await U.api('/api/loyalty/status');
      if (status && status.points_balance > 0) {
        container.classList.remove('hidden');
        const badge = U.$('#co-loyalty-tier-badge');
        const availPts = U.$('#co-loyalty-points-avail');
        const availVnd = U.$('#co-loyalty-money-avail');
        const input = U.$('#co-use-points');
        if (badge) badge.textContent = status.tier_badge;
        if (availPts) availPts.textContent = status.points_balance.toLocaleString('vi-VN');
        if (availVnd) availVnd.textContent = status.points_value_vnd.toLocaleString('vi-VN') + 'đ';
        if (input) {
          input.max = status.points_balance;
          input.value = this.state.usePoints || 0;
        }
        this.state.loyalty = status;

        const applyBtn = U.$('#co-apply-points-btn');
        if (applyBtn && !applyBtn._bound) {
          applyBtn._bound = true;
          applyBtn.addEventListener('click', () => this.applyPoints());
        }
        const maxBtn = U.$('#co-max-points-btn');
        if (maxBtn && !maxBtn._bound) {
          maxBtn._bound = true;
          maxBtn.addEventListener('click', () => this.applyMaxPoints());
        }
      } else {
        container.classList.add('hidden');
        this.state.loyalty = null;
        this.state.usePoints = 0;
      }
    } catch {
      container.classList.add('hidden');
      this.state.loyalty = null;
      this.state.usePoints = 0;
    }
  },

  applyPoints() {
    const input = U.$('#co-use-points');
    const msg = U.$('#co-loyalty-msg');
    const pts = Math.max(0, parseInt(input?.value, 10) || 0);
    const max = this.state.loyalty?.points_balance || 0;
    const finalPts = Math.min(pts, max);
    this.state.usePoints = finalPts;
    if (input) input.value = finalPts;
    this.refreshCart();
    if (msg) {
      msg.classList.remove('hidden');
      msg.textContent = finalPts > 0 ? `Đã dùng ${finalPts} điểm (-${(finalPts * 1000).toLocaleString('vi-VN')}đ)` : 'Không áp dụng điểm';
    }
  },

  applyMaxPoints() {
    const max = this.state.loyalty?.points_balance || 0;
    const input = U.$('#co-use-points');
    if (input) input.value = max;
    this.applyPoints();
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

    const provSelect = U.$('#co-province');
    const wardSelect = U.$('#co-ward');
    const streetInput = U.$('#co-street');

    const provCode = provSelect?.value ? Number(provSelect.value) : null;
    const wardCode = wardSelect?.value ? Number(wardSelect.value) : null;
    const provName = provSelect?.selectedOptions[0]?.dataset?.name || '';
    const wardName = wardSelect?.selectedOptions[0]?.dataset?.name || '';
    const street = streetInput ? streetInput.value.trim() : '';

    if (!provCode) return this.checkoutError('Vui lòng chọn Tỉnh / Thành phố nhận hàng');
    if (!wardCode) return this.checkoutError('Vui lòng chọn Xã / Phường / Đặc khu nhận hàng');
    if (street.length < 3) return this.checkoutError('Vui lòng nhập số nhà, tên đường chi tiết (tối thiểu 3 ký tự)');

    const fullAddress = [street, wardName, provName].filter(Boolean).join(', ');
    if (U.$('#co-address')) U.$('#co-address').value = fullAddress;

    const body = {
      customer_name: U.$('#co-name').value.trim(),
      customer_phone: U.$('#co-phone').value.trim(),
      customer_address: fullAddress,
      province_code: provCode,
      province_name: provName,
      ward_code: wardCode,
      ward_name: wardName,
      specific_address: street || null,
      customer_note: U.$('#co-note').value.trim() || null,
      payment_method: (U.$('input[name="payment"]:checked') || {}).value || 'cod',
      items: this.cartPayload(),
      voucher_code: this.state.quote && this.state.quote.voucher_code || null,
      use_points: this.state.usePoints || 0,
    };
    if (body.customer_name.length < 2) return this.checkoutError('Vui lòng nhập họ tên người nhận');
    if (!/^(?:0|\+?84)\d{9}$/.test(body.customer_phone.replace(/[\s.\-]/g, ''))) return this.checkoutError('Số điện thoại không hợp lệ (ví dụ: 0987654321)');
    if (body.customer_address.length < 8) return this.checkoutError('Vui lòng nhập địa chỉ nhận hàng đầy đủ');

    btn.disabled = true; btn.textContent = 'Đang xử lý...';
    try {
      const order = await U.api('/api/orders', { method: 'POST', body });
      U.store.set('aura_customer', { 
        name: body.customer_name, 
        phone: body.customer_phone, 
        address: body.customer_address,
        province_code: body.province_code,
        province: body.province_name,
        ward_code: body.ward_code,
        ward: body.ward_name,
        specific_address: street 
      });
      this.state.cart = []; this.state.voucher = ''; this.state.usePoints = 0; this.state.quote = null;
      this.saveCart();
      Modal.close('checkout-modal');

      // Nếu khách chọn Thanh toán online VNPay -> Chuyển hướng sang VNPay
      if (body.payment_method === 'vnpay') {
        try {
          const payRes = await U.api('/api/payment/vnpay/create-payment-url', {
            method: 'POST',
            body: { order_id: order.order_id }
          });
          if (payRes && payRes.payment_url) {
            window.location.href = payRes.payment_url;
            return;
          }
        } catch (payErr) {
          console.error('Lỗi tạo URL thanh toán VNPay:', payErr);
        }
      }

      // Đơn hàng COD hoặc hình thức khác -> Chuyển hướng tới trang xác nhận đơn hàng thành công
      window.location.href = `/order-success/${encodeURIComponent(order.order_id)}`;
      return;
    } catch (e) {
      this.checkoutError(e.message);
      this.refreshCart(); // tồn kho/giá có thể đã đổi
    } finally { btn.disabled = false; btn.textContent = 'Đặt hàng'; }
  },

  checkoutError(msg) { const el = U.$('#co-error'); el.textContent = msg; el.classList.remove('hidden'); },

  showSuccess(o) {
    const q = o.quote;
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }

    const isQr = o.payment_method === 'qr_transfer';
    const isVNPay = o.payment_method === 'vnpay';
    const isPaid = o.payment_status === 'paid' || o.status === 'confirmed';

    let paymentHtml = '';
    if (isQr) {
      const qrUrl = o.qr_code_url || `https://img.vietqr.io/image/MB-0900000001-compact2.png?amount=${q.total}&addInfo=AURA%20${encodeURIComponent(o.order_id)}&accountName=AURA%20STUDIO`;
      const bank = o.bank_info || {
        bank_name: 'MBBank (Ngân hàng Quân Đội)',
        account_number: '0900000001',
        account_name: 'AURA STUDIO',
        amount: String(q.total),
        content: `AURA ${o.order_id}`
      };

      paymentHtml = `
      <div id="vietqr-container" class="my-4 rounded-2xl border border-zinc-200 bg-white p-4 text-left shadow-sm">
        <div class="flex items-center justify-between border-b border-zinc-100 pb-3">
          <div class="flex items-center gap-2">
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-red-600 text-white font-extrabold text-xs">V</span>
            <div>
              <h3 class="text-xs font-extrabold text-zinc-900 uppercase tracking-wide">Thanh toán VietQR chuẩn NAPAS 247</h3>
              <p class="text-[10px] text-zinc-500">Mở app Ngân hàng hoặc Ví MoMo/ZaloPay quét mã</p>
            </div>
          </div>
          <span class="rounded bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700 border border-emerald-200">Tự động 24/7</span>
        </div>

        <div class="mt-4 flex flex-col items-center sm:flex-row sm:items-start gap-4">
          <div class="relative flex flex-col items-center">
            <div class="h-48 w-48 overflow-hidden rounded-xl border-2 border-brand-500 bg-white p-2 shadow-sm ${!isPaid ? 'vietqr-live-pulse' : ''}" id="vietqr-image-wrapper">
              <img src="${U.esc(qrUrl)}" alt="VietQR Payment Code" class="h-full w-full object-contain" />
            </div>
            <span class="mt-1 text-[10px] font-mono text-zinc-400">MBBank • Quét mã tự nhận tiền</span>
          </div>

          <div class="flex-1 space-y-2 text-xs w-full">
            <div class="flex items-center justify-between py-1 border-b border-zinc-100">
              <span class="text-zinc-500">Ngân hàng</span>
              <span class="font-bold text-zinc-900">${U.esc(bank.bank_name)}</span>
            </div>
            <div class="flex items-center justify-between py-1 border-b border-zinc-100">
              <span class="text-zinc-500">Số tài khoản</span>
              <div class="flex items-center gap-1.5">
                <span class="font-mono font-bold text-brand-600">${U.esc(bank.account_number)}</span>
                <button type="button" data-action="copy-text" data-text="${U.esc(bank.account_number)}" class="copy-badge-btn" title="Sao chép STK">Sao chép</button>
              </div>
            </div>
            <div class="flex items-center justify-between py-1 border-b border-zinc-100">
              <span class="text-zinc-500">Chủ tài khoản</span>
              <span class="font-bold text-zinc-800">${U.esc(bank.account_name)}</span>
            </div>
            <div class="flex items-center justify-between py-1 border-b border-zinc-100">
              <span class="text-zinc-500">Số tiền</span>
              <div class="flex items-center gap-1.5">
                <span class="font-mono font-black text-rose-600 text-sm">${U.vnd(q.total)}</span>
                <button type="button" data-action="copy-text" data-text="${q.total}" class="copy-badge-btn" title="Sao chép số tiền">Sao chép</button>
              </div>
            </div>
            <div class="flex items-center justify-between py-1">
              <span class="text-zinc-500">Nội dung CK</span>
              <div class="flex items-center gap-1.5">
                <span class="font-mono font-extrabold text-brand-600 bg-brand-50 px-1.5 py-0.5 rounded border border-brand-200">${U.esc(bank.content)}</span>
                <button type="button" data-action="copy-text" data-text="${U.esc(bank.content)}" class="copy-badge-btn" title="Sao chép nội dung">Sao chép</button>
              </div>
            </div>
          </div>
        </div>

        <div id="payment-status-box" class="mt-4 rounded-xl border ${isPaid ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50/80 text-amber-800'} p-3 text-center">
          ${isPaid ? `
            <div class="flex items-center justify-center gap-2 text-xs font-bold text-emerald-800">
              <span class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-white text-xs">✓</span>
              <span>ĐÃ XÁC NHẬN THANH TOÁN THÀNH CÔNG!</span>
            </div>
            <p class="mt-1 text-[11px] text-emerald-700">Hệ thống đã nhận được chuyển khoản. Đơn hàng đang được đóng gói gửi đi.</p>
          ` : `
            <div class="flex items-center justify-center gap-2 text-xs font-bold text-amber-800">
              <span class="flex h-2.5 w-2.5 relative">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-500"></span>
              </span>
              <span id="payment-status-text">Đang chờ chuyển khoản từ ứng dụng ngân hàng...</span>
            </div>
            <p class="mt-1 text-[11px] text-amber-700">Hệ thống tự động kiểm tra trạng thái mỗi 3 giây.</p>
            <div class="mt-2.5 pt-2 border-t border-amber-200/60 flex flex-wrap gap-2 justify-center">
              <button type="button" data-action="pay-vnpay" data-order-id="${U.esc(o.order_id)}"
                      class="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 px-3.5 py-1.5 text-xs font-bold text-white shadow-xs transition hover:scale-[1.02] active:scale-95">
                <span>💳 Thanh toán qua VNPay</span>
              </button>
              <button type="button" data-action="simulate-payment" data-order-id="${U.esc(o.order_id)}"
                      class="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 px-3.5 py-1.5 text-xs font-bold text-white shadow-xs transition hover:scale-[1.02] active:scale-95">
                <span>⚡ Giả lập Chuyển khoản thành công (Test Webhook)</span>
              </button>
            </div>
          `}
        </div>
      </div>`;
    } else if (isVNPay) {
      paymentHtml = `
      <div id="vnpay-container" class="my-4 rounded-2xl border border-blue-200 bg-blue-50/50 p-4 text-left shadow-sm">
        <div class="flex items-center justify-between border-b border-blue-100 pb-3">
          <div class="flex items-center gap-2">
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white font-extrabold text-xs">VNP</span>
            <div>
              <h3 class="text-xs font-extrabold text-blue-900 uppercase tracking-wide">Cổng thanh toán điện tử VNPay</h3>
              <p class="text-[10px] text-zinc-500">ATM nội địa • QR Pay • Thẻ quốc tế Visa/Master</p>
            </div>
          </div>
          <span class="rounded bg-blue-100 px-2 py-0.5 text-[10px] font-bold text-blue-700">VNPay Sandbox</span>
        </div>

        <div id="payment-status-box" class="mt-4 rounded-xl border ${isPaid ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-blue-200 bg-white text-blue-900'} p-3.5 text-center">
          ${isPaid ? `
            <div class="flex items-center justify-center gap-2 text-xs font-bold text-emerald-800">
              <span class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-white text-xs">✓</span>
              <span>ĐÃ XÁC NHẬN THANH TOÁN VNPAY THÀNH CÔNG!</span>
            </div>
            <p class="mt-1 text-[11px] text-emerald-700">Đơn hàng đã được thanh toán và đang được xử lý.</p>
          ` : `
            <div class="flex items-center justify-center gap-2 text-xs font-bold text-blue-800">
              <span class="flex h-2.5 w-2.5 relative">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500"></span>
              </span>
              <span id="payment-status-text">Đang chờ bạn hoàn tất thanh toán trên VNPay...</span>
            </div>
            <p class="mt-1 text-[11px] text-zinc-500">Nhấn nút bên dưới để chuyển sang cổng VNPay nếu trình duyệt chưa tự chuyển.</p>
            <div class="mt-3 flex flex-wrap gap-2 justify-center">
              <button type="button" data-action="pay-vnpay" data-order-id="${U.esc(o.order_id)}"
                      class="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 px-4 py-2 text-xs font-bold text-white shadow-xs transition hover:scale-[1.02] active:scale-95">
                <span>💳 Thanh toán qua VNPay ngay</span>
              </button>
            </div>
          `}
        </div>
      </div>`;
    }

    U.$('#success-body').innerHTML = `
    <div class="p-5 sm:p-6 text-center max-w-lg mx-auto">
      <div class="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full ${isPaid ? 'bg-emerald-100 text-emerald-600' : 'bg-brand-100 text-brand-600'}">
        <svg class="h-7 w-7" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>
      </div>
      <h2 class="text-base sm:text-lg font-bold text-zinc-900" id="success-title">
        ${isPaid ? 'Đặt hàng & Thanh toán thành công!' : (isVNPay ? 'Đã ghi nhận đơn • Vui lòng thanh toán VNPay' : (isQr ? 'Đã ghi nhận đơn • Vui lòng chuyển khoản' : 'Đặt hàng thành công!'))}
      </h2>
      <p class="text-xs text-zinc-500 mt-0.5">Mã đơn hàng: <b class="font-mono text-brand-600">${U.esc(o.order_id)}</b></p>
      
      ${paymentHtml}

      <div class="my-3 space-y-1.5 rounded-xl border border-zinc-200 bg-zinc-50/70 p-3.5 text-left text-xs">
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Người nhận</span><b class="text-right text-zinc-800">${U.esc(o.customer_name)} (${U.esc(o.customer_phone)})</b></div>
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Địa chỉ giao</span><span class="text-right font-medium text-zinc-800">${U.esc(o.customer_address)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-zinc-500">Hình thức</span><b>${isVNPay ? 'VNPay Sandbox' : (isQr ? 'Chuyển khoản VietQR' : 'Thanh toán khi nhận hàng (COD)')}</b></div>
        ${this.summaryRows(q)}
      </div>

      <div class="mt-4 flex gap-2">
        <a href="/profile" class="btn btn-soft flex-1 !py-2.5 text-xs text-center">Xem đơn trong Tài khoản</a>
        <button data-action="close-modal" data-target="success-modal" class="btn btn-primary flex-1 !py-2.5 uppercase tracking-wide text-xs">Tiếp tục mua sắm</button>
      </div>
    </div>`;

    Modal.open('success-modal');

    // Bắt đầu Polling kiểm tra trạng thái thanh toán tự động nếu chưa thanh toán
    if ((isQr || isVNPay) && !isPaid) {
      this._pollTimer = setInterval(async () => {
        try {
          const res = await U.api(`/api/payment/check-status/${encodeURIComponent(o.order_id)}`);
          if (res && (res.status === 'paid' || res.order_status === 'confirmed')) {
            clearInterval(App._pollTimer);
            App._pollTimer = null;
            App.onPaymentSuccess(o.order_id);
          }
        } catch { /* tiếp tục polling */ }
      }, 3000);
    }
  },

  onPaymentSuccess(orderId) {
    U.toast(`Đơn hàng ${orderId} đã được xác nhận thanh toán thành công!`, 'ok');
    const title = U.$('#success-title');
    if (title) title.textContent = 'Đặt hàng & Thanh toán thành công!';

    const box = U.$('#payment-status-box');
    if (box) {
      box.className = 'mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3.5 text-center';
      box.innerHTML = `
        <div class="flex items-center justify-center gap-2 text-xs font-bold text-emerald-800">
          <span class="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-white text-xs">✓</span>
          <span>ĐÃ XÁC NHẬN THANH TOÁN THÀNH CÔNG (PAID)!</span>
        </div>
        <p class="mt-1 text-[11px] text-emerald-700">Hệ thống đã nhận được tiền từ giao dịch VietQR. Đơn hàng đang được đóng gói gửi đi!</p>
      `;
    }

    const imgWrapper = U.$('#vietqr-image-wrapper');
    if (imgWrapper) {
      imgWrapper.classList.remove('vietqr-live-pulse');
      imgWrapper.classList.add('border-emerald-500');
    }
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

/* ===================== Giai đoạn 2: SizeChartUI, TrackingUI & InvoiceUI ===================== */
const SizeChartUI = {
  currentProductId: null,
  activeTab: 'specs',

  async open(productId) {
    this.currentProductId = productId;
    this.activeTab = 'specs';
    this.switchTab('specs');
    Modal.open('size-chart-modal');

    const titleEl = U.$('#sc-modal-title');
    const subtitleEl = U.$('#sc-modal-subtitle');
    const tableContainer = U.$('#sc-table-container');
    const guideContainer = U.$('#sc-guide-cards');
    const careContainer = U.$('#sc-tab-care');

    if (tableContainer) tableContainer.innerHTML = '<div class="py-8 text-center text-xs text-zinc-400">Đang tải bảng số đo thực tế...</div>';

    try {
      const data = await U.api(`/api/products/${encodeURIComponent(productId)}/size-chart`);
      if (titleEl) titleEl.textContent = `Bảng số đo chi tiết: ${data.product_name}`;
      if (subtitleEl) subtitleEl.textContent = `Danh mục: ${data.category_name} • Đơn vị đo: ${data.unit}`;

      // 1. Render Table
      if (tableContainer) {
        const cols = data.columns || [];
        const rows = data.rows || [];
        let html = '<table class="size-chart-table"><thead><tr>';
        cols.forEach(c => { html += `<th>${U.esc(c)}</th>`; });
        html += '</tr></thead><tbody>';

        rows.forEach(r => {
          html += '<tr>';
          cols.forEach(c => {
            if (c === 'Size') {
              html += `<td class="font-bold text-brand-600 bg-brand-50/40">${U.esc(r.size)}</td>`;
            } else {
              const val = (r.specs && r.specs[c]) || '-';
              html += `<td>${U.esc(val)}</td>`;
            }
          });
          html += '</tr>';
        });
        html += '</tbody></table>';
        tableContainer.innerHTML = html;
      }

      // 2. Render Guide
      if (guideContainer) {
        const guides = data.measuring_guide || [];
        guideContainer.innerHTML = guides.map(g => `
          <div class="rounded-xl border border-zinc-200 bg-white p-3 space-y-1.5 shadow-2xs">
            <h4 class="text-xs font-bold text-zinc-900 flex items-center gap-1.5">
              <span class="flex h-5 w-5 items-center justify-center rounded-full bg-brand-100 text-brand-700 text-[10px]">📏</span>
              ${U.esc(g.part)}
            </h4>
            <p class="text-[11px] text-zinc-600 leading-relaxed">${U.esc(g.how_to)}</p>
            <p class="text-[10px] text-amber-700 font-medium bg-amber-50 rounded px-2 py-1">💡 ${U.esc(g.tip)}</p>
          </div>
        `).join('');
      }

      // 3. Render Care
      if (careContainer) {
        const cares = data.care_instructions || [];
        careContainer.innerHTML = cares.map(c => `
          <div class="flex items-start gap-2 text-xs text-zinc-700 bg-zinc-50 rounded-lg p-2.5">
            <span class="text-brand-600 font-bold">✓</span>
            <span>${U.esc(c)}</span>
          </div>
        `).join('');
      }
    } catch (e) {
      if (tableContainer) tableContainer.innerHTML = `<div class="py-8 text-center text-xs text-red-500">Lỗi khi tải bảng size: ${U.esc(e.message)}</div>`;
    }
  },

  switchTab(tab) {
    this.activeTab = tab;
    ['specs', 'guide', 'care'].forEach(t => {
      const btn = U.$(`#sc-tab-${t}-btn`);
      const pane = U.$(`#sc-tab-${t}`);
      if (btn) {
        if (t === tab) {
          btn.className = 'border-b-2 border-brand-600 px-4 py-2.5 text-brand-600 font-bold transition';
        } else {
          btn.className = 'border-b-2 border-transparent px-4 py-2.5 text-zinc-500 hover:text-zinc-800 font-medium transition';
        }
      }
      if (pane) {
        if (t === tab) pane.classList.remove('hidden');
        else pane.classList.add('hidden');
      }
    });
  }
};
window.SizeChartUI = SizeChartUI;

const TrackingUI = {
  currentOrder: null,

  openModal(code = '') {
    Modal.open('tracking-modal');
    const input = U.$('#tracking-input');
    if (input) {
      if (code) {
        input.value = code;
        this.doSearch();
      } else {
        input.focus();
      }
    }
  },

  async doSearch() {
    const input = U.$('#tracking-input');
    const code = input ? input.value.trim() : '';
    if (!code) return;

    const resBox = U.$('#tracking-result');
    const errBox = U.$('#tracking-error');
    const submitBtn = U.$('#tracking-submit-btn');

    if (errBox) errBox.classList.add('hidden');
    if (resBox) resBox.classList.add('hidden');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Đang tìm...'; }

    try {
      const data = await U.api(`/api/orders/track/${encodeURIComponent(code)}`);
      this.currentOrder = data;

      // Điền thông tin kiện hàng
      U.$('#tr-code').textContent = data.tracking_code;
      U.$('#tr-carrier').textContent = data.carrier;
      U.$('#tr-est-date').textContent = data.estimated_delivery;
      U.$('#tr-cust-name').textContent = data.customer_name;
      U.$('#tr-cust-phone').textContent = data.customer_phone;
      U.$('#tr-cust-addr').textContent = data.customer_address;
      U.$('#tr-payment').textContent = `${data.payment_method} (${data.payment_status})`;

      // Status badge
      const badge = U.$('#tr-status-badge');
      if (badge) {
        badge.textContent = data.shipping_status_label;
        if (data.shipping_status === 'delivered') {
          badge.className = 'rounded-full px-2.5 py-0.5 text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300';
        } else if (data.shipping_status === 'ready_to_pick' || data.shipping_status === 'pending_confirm') {
          badge.className = 'rounded-full px-2.5 py-0.5 text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300';
        } else {
          badge.className = 'rounded-full px-2.5 py-0.5 text-[10px] font-bold bg-blue-100 text-blue-800 border border-blue-300';
        }
      }

      // Stepper Timeline 6 bước
      const list = U.$('#tr-timeline-list');
      if (list && data.timeline) {
        list.innerHTML = data.timeline.map(st => {
          const isDone = st.status === 'completed';
          const isCurr = st.status === 'current';
          const nodeIcon = isDone ? '✓' : isCurr ? '●' : '○';
          return `
            <div class="timeline-step ${st.status}">
              <div class="timeline-node font-bold">${nodeIcon}</div>
              <div class="space-y-0.5">
                <div class="flex items-baseline justify-between gap-2">
                  <h4 class="text-xs font-bold ${isDone ? 'text-zinc-900' : isCurr ? 'text-brand-600' : 'text-zinc-400'}">${U.esc(st.title)}</h4>
                  <span class="text-[10px] font-mono text-zinc-400 whitespace-nowrap">${U.esc(st.time)}</span>
                </div>
                <p class="text-[11px] ${isDone || isCurr ? 'text-zinc-600' : 'text-zinc-400'}">${U.esc(st.description)}</p>
                <p class="text-[10px] text-zinc-400 font-medium">📍 ${U.esc(st.location)}</p>
              </div>
            </div>
          `;
        }).join('');
      }

      if (resBox) resBox.classList.remove('hidden');
    } catch (e) {
      if (errBox) {
        errBox.textContent = e.message || 'Không tìm thấy đơn hàng. Vui lòng kiểm tra lại mã hoặc số điện thoại.';
        errBox.classList.remove('hidden');
      }
    } finally {
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Tra cứu'; }
    }
  },

  openInvoice() {
    if (!this.currentOrder || !this.currentOrder.order_id) return;
    InvoiceUI.open(this.currentOrder.order_id);
  },

  async sendNotify() {
    if (!this.currentOrder || !this.currentOrder.order_id) return;
    const btn = U.$('#tr-notify-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Đang gửi...'; }
    try {
      const res = await U.api(`/api/orders/${encodeURIComponent(this.currentOrder.order_id)}/send-notification?channel=zalo`, { method: 'POST' });
      U.toast(res.message || 'Đã gửi thông báo tiến độ thành công!');
    } catch (e) {
      U.toast(e.message || 'Lỗi gửi thông báo', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<span>📲</span> Giả lập SMS/Zalo cập nhật'; }
    }
  }
};
window.TrackingUI = TrackingUI;

const InvoiceUI = {
  async open(orderId) {
    Modal.open('invoice-modal');
    try {
      const inv = await U.api(`/api/orders/${encodeURIComponent(orderId)}/invoice`);
      U.$('#inv-number').textContent = inv.invoice_number;
      U.$('#inv-date').textContent = inv.issued_at;
      U.$('#inv-buyer-name').textContent = inv.buyer.name;
      U.$('#inv-buyer-phone').textContent = inv.buyer.phone;
      U.$('#inv-buyer-addr').textContent = inv.buyer.address;
      U.$('#inv-carrier').textContent = inv.carrier;
      U.$('#inv-tracking').textContent = inv.tracking_code;
      U.$('#inv-pay-method').textContent = inv.payment_method;
      U.$('#inv-pay-status').textContent = inv.payment_status;

      U.$('#inv-subtotal').textContent = U.vnd(inv.subtotal);
      U.$('#inv-ship-fee').textContent = U.vnd(inv.shipping_fee);
      U.$('#inv-discount').textContent = `-${U.vnd(inv.discount_amount)}`;
      U.$('#inv-vat').textContent = `${U.vnd(inv.vat_amount)} (${inv.vat_rate}%)`;
      U.$('#inv-total').textContent = U.vnd(inv.total_amount);

      const itemsBody = U.$('#inv-items-body');
      if (itemsBody) {
        itemsBody.innerHTML = (inv.items || []).map((it, idx) => `
          <tr class="hover:bg-zinc-50">
            <td class="py-2.5 px-2.5 text-center text-zinc-500 font-mono">${idx + 1}</td>
            <td class="py-2.5 px-2.5 font-semibold text-zinc-900">${U.esc(it.name)}</td>
            <td class="py-2.5 px-2.5 text-center text-zinc-600">${U.esc(it.color)} / ${U.esc(it.size)}</td>
            <td class="py-2.5 px-2.5 text-center font-bold text-zinc-900">${it.quantity}</td>
            <td class="py-2.5 px-2.5 text-right font-mono text-zinc-700">${U.vnd(it.unit_price)}</td>
            <td class="py-2.5 px-2.5 text-right font-mono font-bold text-zinc-900">${U.vnd(it.line_total)}</td>
          </tr>
        `).join('');
      }
    } catch (e) {
      U.toast('Không thể tải hóa đơn: ' + e.message, 'error');
    }
  }
};
window.InvoiceUI = InvoiceUI;

/* ===================== Giai đoạn 3: Phục hồi giỏ hàng & CSKH Đa kênh ===================== */
const ExitIntentUI = {
  _timer: null,
  _triggered: false,

  init() {
    if (sessionStorage.getItem('aura_exit_intent_shown')) return;

    // Trigger 1: Chuột rời cửa sổ lên trên (di chuyển chuột ra thanh địa chỉ hoặc nút đóng tab)
    document.addEventListener('mouseleave', (e) => {
      if (e.clientY <= 15) {
        this.trigger();
      }
    });

    // Trigger 2: Khách dừng thao tác (idle) 45s khi giỏ hàng có sản phẩm
    this.resetTimer();
    ['mousemove', 'keydown', 'scroll', 'click'].forEach(evt => {
      document.addEventListener(evt, () => this.resetTimer(), { passive: true });
    });
  },

  resetTimer() {
    if (this._triggered || sessionStorage.getItem('aura_exit_intent_shown')) return;
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this.trigger();
    }, 45000);
  },

  trigger() {
    if (this._triggered) return;
    if (sessionStorage.getItem('aura_exit_intent_shown')) return;
    if (!App.state.cart || App.state.cart.length === 0) return;
    const checkoutModal = U.$('#checkout-modal');
    if (checkoutModal && !checkoutModal.classList.contains('hidden')) return;

    this._triggered = true;
    sessionStorage.setItem('aura_exit_intent_shown', '1');
    if (this._timer) clearTimeout(this._timer);

    Modal.open('exit-intent-modal');
  },

  applyAndCheckout() {
    Modal.close('exit-intent-modal');
    App.state.voucher = 'STAYWITHUS';
    const coVoucher = U.$('#co-voucher');
    if (coVoucher) coVoucher.value = 'STAYWITHUS';
    App.openCheckout();
    App.applyVoucher();
    U.toast('Đã áp dụng mã STAYWITHUS giảm thêm 5%!');
  }
};
window.ExitIntentUI = ExitIntentUI;

const OmnichannelUI = {
  isOpen: false,

  toggle() {
    this.isOpen = !this.isOpen;
    const menu = U.$('#omnichannel-menu');
    const icon = U.$('#omnichannel-fab-icon');
    if (menu) {
      if (this.isOpen) {
        menu.classList.remove('hidden');
      } else {
        menu.classList.add('hidden');
      }
    }
    if (icon) {
      icon.textContent = this.isOpen ? '✕' : '🎧';
    }
  },

  close() {
    this.isOpen = false;
    const menu = U.$('#omnichannel-menu');
    const icon = U.$('#omnichannel-fab-icon');
    if (menu) menu.classList.add('hidden');
    if (icon) icon.textContent = '🎧';
  },

  init() {
    document.addEventListener('click', (e) => {
      if (this.isOpen && !e.target.closest('#omnichannel-widget')) {
        this.close();
      }
    });
  }
};
window.OmnichannelUI = OmnichannelUI;

/* ===================== Đăng ký hành động ===================== */
Object.assign(Actions, {
  'open-size-chart': d => SizeChartUI.open(d.productId || (App.state.qv && App.state.qv.product ? App.state.qv.product.id : 'prod_001')),
  'open-tracking': d => TrackingUI.openModal(d.code || ''),
  'open-invoice': d => InvoiceUI.open(d.orderId),
  'close-modal': d => {
    if (d.target === 'success-modal' && App._pollTimer) {
      clearInterval(App._pollTimer);
      App._pollTimer = null;
    }
    Modal.close(d.target);
  },
  'pay-vnpay': async (d, btn) => {
    if (!d.orderId) return;
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Đang chuyển tới VNPay...'; }
      U.toast('Đang tạo liên kết thanh toán VNPay...');
      const res = await U.api('/api/payment/vnpay/create-payment-url', {
        method: 'POST',
        body: { order_id: d.orderId }
      });
      if (res && res.payment_url) {
        window.location.href = res.payment_url;
      } else {
        throw new Error('Không nhận được liên kết thanh toán từ máy chủ');
      }
    } catch (e) {
      U.toast(e.message || 'Lỗi khi tạo liên kết thanh toán VNPay', 'error');
      if (btn) { btn.disabled = false; btn.textContent = '💳 Thanh toán qua VNPay'; }
    }
  },
  'simulate-payment': async (d, btn) => {
    if (!d.orderId) return;
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Đang gửi Webhook...'; }
      U.toast('Đang gửi Webhook xác nhận thanh toán...');
      const res = await U.api(`/api/payment/simulate-success/${encodeURIComponent(d.orderId)}`, { method: 'POST' });
      if (res && res.status === 'paid') {
        if (App._pollTimer) { clearInterval(App._pollTimer); App._pollTimer = null; }
        App.onPaymentSuccess(d.orderId);
      }
    } catch (e) {
      U.toast(e.message || 'Lỗi khi kích hoạt webhook', 'error');
      if (btn) { btn.disabled = false; btn.textContent = '⚡ Giả lập Chuyển khoản thành công (Test Webhook)'; }
    }
  },
  'copy-text': d => {
    if (d.text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(d.text);
      }
      U.toast(`Đã sao chép: ${d.text}`);
    }
  },
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
  'click-trend': d => {
    const kw = d.kw;
    if (!kw) return;
    const input = U.$('#search-input');
    if (input) input.value = kw;
    App.state.filters.search = kw;
    App.loadProducts();
    const catalog = U.$('#catalog');
    if (catalog) catalog.scrollIntoView({ behavior: 'smooth' });
    U.toast(`Đang lọc sản phẩm theo xu hướng: ${kw}`);
  },
  'refresh-trends': async () => {
    try {
      U.toast('Đang làm mới dữ liệu xu hướng...');
      await U.api('/api/trends/refresh', { method: 'POST' });
      await App.loadTrending(true);
      U.toast('Đã cập nhật xu hướng mới nhất!', 'ok');
    } catch (e) {
      U.toast('Không thể làm mới: ' + e.message, 'error');
    }
  },
  'auth-logout': async () => {
    try {
      await U.api('/api/auth/logout', { method: 'POST' });
      localStorage.removeItem('aura_token');
      localStorage.removeItem('aura_user');
      U.toast('Đã đăng xuất thành công!');
      setTimeout(() => location.reload(), 400);
    } catch {
      location.reload();
    }
  },
});
document.addEventListener('change', e => { // đổi size/màu ngay trong giỏ
  const t = e.target;
  if (t.dataset && t.dataset.cartVariant) App.changeVariant(Number(t.dataset.idx), t.dataset.cartVariant, t.value);
});
document.addEventListener('keydown', e => { // thẻ video truy cập bằng bàn phím
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('.reel-card')) { e.preventDefault(); e.target.click(); }
});

document.addEventListener('DOMContentLoaded', () => App.init());
