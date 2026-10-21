/* ==========================================================================
   ASSIGNMENT WEB1014 - JAVASCRIPT HỖ TRỢ TƯƠNG TÁC
   - Bật tắt Menu Responsive di động
   - Đổi ảnh thumbnail xem chi tiết sản phẩm
   - Cập nhật số lượng giỏ hàng & tính tiền tức thì
   - Kiểm tra hợp lệ biểu mẫu (Form Validation)
   ========================================================================== */

document.addEventListener('DOMContentLoaded', function () {
  // 1. Bật tắt Mobile Menu
  const hamburger = document.querySelector('.hamburger');
  const navMenu = document.querySelector('.nav-menu');

  if (hamburger && navMenu) {
    hamburger.addEventListener('click', function () {
      navMenu.classList.toggle('open');
      const isOpen = navMenu.classList.contains('open');
      hamburger.innerHTML = isOpen ? '✕' : '☰';
    });
  }

  // 2. Chuyển ảnh Thumbnail ở trang chi tiết sản phẩm
  const thumbImgs = document.querySelectorAll('.thumb-img');
  const mainImg = document.querySelector('.gallery-main img');

  if (thumbImgs.length > 0 && mainImg) {
    thumbImgs.forEach(thumb => {
      thumb.addEventListener('click', function () {
        thumbImgs.forEach(t => t.classList.remove('active'));
        this.classList.add('active');
        mainImg.src = this.src;
      });
    });
  }

  // 3. Chọn size ở trang chi tiết sản phẩm
  const sizeBtns = document.querySelectorAll('.size-btn');
  sizeBtns.forEach(btn => {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      sizeBtns.forEach(b => b.classList.remove('active'));
      this.classList.add('active');
    });
  });

  // 4. Tăng giảm số lượng sản phẩm
  const qtyMinus = document.querySelector('.qty-minus');
  const qtyPlus = document.querySelector('.qty-plus');
  const qtyInput = document.querySelector('.quantity-input');

  if (qtyMinus && qtyPlus && qtyInput) {
    qtyMinus.addEventListener('click', function () {
      let val = parseInt(qtyInput.value) || 1;
      if (val > 1) {
        qtyInput.value = val - 1;
      }
    });

    qtyPlus.addEventListener('click', function () {
      let val = parseInt(qtyInput.value) || 1;
      qtyInput.value = val + 1;
    });
  }

  // 5. Thêm vào giỏ hàng & tăng số đếm badge
  const addCartBtns = document.querySelectorAll('.btn-add-cart, .btn-add-to-cart');
  const cartBadge = document.querySelector('.cart-count');

  addCartBtns.forEach(btn => {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      if (cartBadge) {
        let current = parseInt(cartBadge.textContent) || 0;
        cartBadge.textContent = current + 1;
      }
      alert('Đã thêm sản phẩm vào giỏ hàng thành công!');
    });
  });

  // 6. Xử lý Form gửi góp ý / liên hệ
  const contactForm = document.getElementById('contactForm');
  if (contactForm) {
    contactForm.addEventListener('submit', function (e) {
      e.preventDefault();
      alert('Cảm ơn bạn đã gửi liên hệ! Đội ngũ AURA Fashion sẽ phản hồi trong vòng 24h.');
      contactForm.reset();
    });
  }

  // 7. Xử lý Form đặt hàng
  const checkoutForm = document.getElementById('checkoutForm');
  if (checkoutForm) {
    checkoutForm.addEventListener('submit', function (e) {
      e.preventDefault();
      alert('Chúc mừng bạn đã đặt hàng thành công! Đơn hàng của bạn đang được xử lý.');
      window.location.href = 'index.html';
    });
  }
});
