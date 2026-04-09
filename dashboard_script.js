/* Sprint Health Dashboard — Interaction Logic & Particles */

(() => {
  const storageKey = 'sprint-health-theme';
  const themeToggle = document.getElementById('themeToggle');
  const themeToggleText = document.getElementById('themeToggleText');
  const themeToggleIcon = document.getElementById('themeToggleIcon');

  function applyTheme(theme) {
    document.body.dataset.theme = theme;
    if (themeToggleText) themeToggleText.textContent = theme === 'light' ? 'Dark Mode' : 'Light Mode';
    if (themeToggleIcon) themeToggleIcon.textContent = theme === 'light' ? 'DM' : 'LM';
  }

  const savedTheme = localStorage.getItem(storageKey);
  const preferredTheme = savedTheme || 'light';
  applyTheme(preferredTheme);

  themeToggle?.addEventListener('click', () => {
    const nextTheme = document.body.dataset.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem(storageKey, nextTheme);
    applyTheme(nextTheme);
  });

  const particleCanvas = document.getElementById('reportParticles');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  if (particleCanvas) {
    const particleCtx = particleCanvas.getContext('2d', { alpha: true });
    const DPR_LIMIT = 1.8;
    const iconSprites = [];
    const particles = [];
    let canvasWidth = 0;
    let canvasHeight = 0;
    let centerX = 0;
    let centerY = 0;
    let baseRadius = 0;
    let orbitTime = 0;
    let frameId = 0;
    let startedAt = performance.now();

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function easeOutCubic(t) {
      return 1 - Math.pow(1 - t, 3);
    }

    function particlePalette() {
      return document.body.dataset.theme === 'light'
        ? {
            ink: '#172B4D',
            brand: '#0052CC',
            muted: 'rgba(23, 43, 77, 0.22)',
            glow: 'rgba(0, 82, 204, 0.09)',
            coreA: 'rgba(255,255,255,0.95)',
            coreB: 'rgba(76,154,255,0.18)'
          }
        : {
            ink: '#DCE9FF',
            brand: '#4C9AFF',
            muted: 'rgba(220, 233, 255, 0.22)',
            glow: 'rgba(76, 154, 255, 0.12)',
            coreA: 'rgba(255,255,255,0.82)',
            coreB: 'rgba(76, 154, 255, 0.20)'
          };
    }

    function makeSprite(drawFn, size) {
      const offscreen = document.createElement('canvas');
      offscreen.width = size;
      offscreen.height = size;
      const ictx = offscreen.getContext('2d');
      drawFn(ictx, size);
      return offscreen;
    }

    function rebuildSprites() {
      const palette = particlePalette();
      iconSprites.length = 0;
      iconSprites.push(
        makeSprite((ictx, size) => {
          ictx.strokeStyle = palette.brand;
          ictx.lineWidth = size * 0.11;
          ictx.lineCap = 'round';
          ictx.lineJoin = 'round';
          ictx.beginPath();
          ictx.moveTo(size * 0.24, size * 0.54);
          ictx.lineTo(size * 0.43, size * 0.72);
          ictx.lineTo(size * 0.76, size * 0.30);
          ictx.stroke();
        }, 48),
        makeSprite((ictx, size) => {
          ictx.fillStyle = palette.brand;
          ictx.beginPath();
          ictx.moveTo(size * 0.50, size * 0.10);
          ictx.lineTo(size * 0.82, size * 0.30);
          ictx.lineTo(size * 0.82, size * 0.70);
          ictx.lineTo(size * 0.50, size * 0.90);
          ictx.lineTo(size * 0.18, size * 0.70);
          ictx.lineTo(size * 0.18, size * 0.30);
          ictx.closePath();
          ictx.fill();
          ictx.clearRect(size * 0.39, size * 0.29, size * 0.22, size * 0.42);
        }, 48),
        makeSprite((ictx, size) => {
          ictx.strokeStyle = palette.ink;
          ictx.lineWidth = size * 0.10;
          ictx.lineCap = 'round';
          ictx.beginPath();
          ictx.moveTo(size * 0.24, size * 0.38);
          ictx.lineTo(size * 0.76, size * 0.38);
          ictx.moveTo(size * 0.24, size * 0.52);
          ictx.lineTo(size * 0.64, size * 0.52);
          ictx.moveTo(size * 0.24, size * 0.66);
          ictx.lineTo(size * 0.58, size * 0.66);
          ictx.stroke();
        }, 48)
      );
    }

    function resizeParticles() {
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_LIMIT);
      canvasWidth = window.innerWidth;
      canvasHeight = window.innerHeight;
      centerX = canvasWidth * 0.5;
      centerY = Math.min(380, canvasHeight * 0.27);
      baseRadius = Math.min(canvasWidth, canvasHeight) * 0.14;
      particleCanvas.width = Math.round(canvasWidth * dpr);
      particleCanvas.height = Math.round(canvasHeight * dpr);
      particleCanvas.style.width = `${canvasWidth}px`;
      particleCanvas.style.height = `${canvasHeight}px`;
      particleCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      rebuildSprites();
      rebuildParticles();
    }

    function rebuildParticles() {
      const palette = particlePalette();
      const count = canvasWidth < 900 ? 110 : 150;
      particles.length = 0;
      for (let i = 0; i < count; i += 1) {
        const ratio = i / count;
        const orbitRadius = 28 + Math.pow(ratio, 1.32) * Math.min(canvasWidth, canvasHeight) * 0.42;
        const isIcon = i % 12 === 0;
        particles.push({
          angle: Math.random() * Math.PI * 2,
          orbitRadius,
          speed: 0.00045 + Math.random() * 0.0012,
          twist: 0.8 + Math.random() * 1.4,
          drift: (Math.random() - 0.5) * 0.12,
          size: isIcon ? 10 + Math.random() * 6 : 1.8 + Math.random() * 3.2,
          alpha: isIcon ? 0.34 + Math.random() * 0.16 : 0.16 + Math.random() * 0.24,
          sprite: isIcon ? iconSprites[i % iconSprites.length] : null,
          color: i % 4 === 0 ? palette.brand : palette.muted
        });
      }
    }

    function drawGlow(pulse) {
      const palette = particlePalette();
      const gradient = particleCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 1.8);
      gradient.addColorStop(0, palette.glow);
      gradient.addColorStop(0.46, document.body.dataset.theme === 'light' ? 'rgba(0,82,204,0.04)' : 'rgba(76,154,255,0.06)');
      gradient.addColorStop(1, 'rgba(0,0,0,0)');
      particleCtx.fillStyle = gradient;
      particleCtx.beginPath();
      particleCtx.arc(centerX, centerY, baseRadius * (1.08 + pulse * 0.05), 0, Math.PI * 2);
      particleCtx.fill();
    }

    function drawCore(pulse) {
      const palette = particlePalette();
      const gradient = particleCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 0.48);
      gradient.addColorStop(0, palette.coreA);
      gradient.addColorStop(0.26, palette.coreB);
      gradient.addColorStop(1, 'rgba(0,0,0,0)');
      particleCtx.fillStyle = gradient;
      particleCtx.beginPath();
      particleCtx.arc(centerX, centerY, baseRadius * (0.12 + pulse * 0.012), 0, Math.PI * 2);
      particleCtx.fill();
    }

    function drawParticle(particle, elapsed, pulse) {
      const burst = easeOutCubic(clamp(elapsed / 2200, 0, 1));
      const angle = particle.angle + elapsed * particle.speed * particle.twist;
      const orbit = particle.orbitRadius * (0.84 + burst * 0.16);
      const x = centerX + Math.cos(angle + particle.drift) * orbit;
      const y = centerY + Math.sin(angle) * orbit * 0.72;

      particleCtx.save();
      particleCtx.translate(x, y);
      particleCtx.rotate(angle * 0.82);
      particleCtx.globalAlpha = particle.alpha * (0.88 + pulse * 0.12);

      if (particle.sprite) {
        const size = particle.size * (1 + pulse * 0.03);
        particleCtx.drawImage(particle.sprite, -size / 2, -size / 2, size, size);
      } else {
        particleCtx.fillStyle = particle.color;
        particleCtx.beginPath();
        particleCtx.arc(0, 0, particle.size, 0, Math.PI * 2);
        particleCtx.fill();
      }

      particleCtx.restore();
    }

    function renderParticles(now) {
      particleCtx.clearRect(0, 0, canvasWidth, canvasHeight);
      orbitTime += 16.67;
      const pulse = 0.5 + Math.sin((now - startedAt) * 0.0024) * 0.5;
      drawGlow(pulse);
      for (let i = 0; i < particles.length; i += 1) {
        drawParticle(particles[i], orbitTime, pulse);
      }
      drawCore(pulse);

      if (!prefersReducedMotion.matches) {
        frameId = window.requestAnimationFrame(renderParticles);
      }
    }

    function startParticles() {
      window.cancelAnimationFrame(frameId);
      startedAt = performance.now();
      orbitTime = 0;
      resizeParticles();
      renderParticles(startedAt);
    }

    startParticles();
    window.addEventListener('resize', resizeParticles, { passive: true });
    prefersReducedMotion.addEventListener('change', startParticles);
  }

  const roots = Array.from(document.querySelectorAll('.interactive-activity-shell'));
  roots.forEach((root) => {
    const searchInput = root.querySelector('.qa-search-input');
    const dateDropdown = root.querySelector('[data-date-dropdown="true"]');
    const dateTrigger = dateDropdown?.querySelector('.activity-date-trigger');
    const dateValue = dateDropdown?.querySelector('[data-date-value]');
    const dateOptions = Array.from(dateDropdown?.querySelectorAll('[data-date-option]') || []);
    const panes = Array.from(root.querySelectorAll('.activity-date-pane'));
    let activeFilter = 'all';
    let activeDate = dateOptions.find((option) => option.classList.contains('active'))?.dataset.dateOption || panes[0]?.dataset.date || '';

    function getActivePane() {
      return panes.find((pane) => pane.dataset.date === activeDate) || panes[0] || null;
    }

    function applyFilters() {
      const term = (searchInput?.value || '').trim().toLowerCase();
      const activePane = getActivePane();
      panes.forEach((pane) => pane.classList.toggle('active', pane === activePane));
      if (dateValue) {
        const activeOption = dateOptions.find((option) => option.dataset.dateOption === activeDate);
        if (activeOption) dateValue.textContent = activeOption.textContent || '';
      }
      dateOptions.forEach((option) => option.classList.toggle('active', option.dataset.dateOption === activeDate));
      if (!activePane) return;

      const tabs = Array.from(activePane.querySelectorAll('.qa-tab[data-filter]'));
      tabs.forEach((item) => item.classList.toggle('active', (item.dataset.filter || 'all') === activeFilter));

      const sections = Array.from(activePane.querySelectorAll('.qa-tester-section'));
      sections.forEach((section) => {
        const cards = Array.from(section.querySelectorAll('[data-activity-card="true"]'));
        const showMoreButton = section.querySelector('.qa-show-more');
        const expandLimit = Number(showMoreButton?.dataset.expand || '6');
        const expanded = showMoreButton?.dataset.expanded === 'true';

        let visibleCount = 0;
        cards.forEach((card) => {
          const matchesFilter = activeFilter === 'all' || card.dataset.type === activeFilter;
          const matchesSearch = !term || (card.dataset.search || '').includes(term);
          const matches = matchesFilter && matchesSearch;
          card.classList.toggle('hidden-by-filter', !matches);

          if (!matches) {
            card.classList.add('hidden-by-limit');
            return;
          }

          visibleCount += 1;
          card.classList.toggle('hidden-by-limit', !expanded && visibleCount > expandLimit);
        });

        const hiddenMatching = cards.filter((card) =>
          !card.classList.contains('hidden-by-filter') && card.classList.contains('hidden-by-limit')
        ).length;

        if (showMoreButton) {
          if (hiddenMatching > 0) {
            showMoreButton.classList.remove('hidden');
            showMoreButton.innerHTML = `Show More <span>+${hiddenMatching}</span>`;
          } else {
            showMoreButton.classList.add('hidden');
          }
        }

        section.classList.toggle('hidden-by-filter', visibleCount === 0);
      });
    }

    root.addEventListener('click', (event) => {
      const tab = event.target.closest('.qa-tab[data-filter]');
      if (tab && root.contains(tab)) {
        activeFilter = tab.dataset.filter || 'all';
        applyFilters();
        return;
      }
      const dateOption = event.target.closest('[data-date-option]');
      if (dateOption && root.contains(dateOption)) {
        activeDate = dateOption.dataset.dateOption || activeDate;
        activeFilter = 'all';
        dateDropdown?.classList.remove('open');
        root.closest('.card')?.classList.remove('dropdown-open');
        dateTrigger?.setAttribute('aria-expanded', 'false');
        applyFilters();
        return;
      }
      const showMore = event.target.closest('.qa-show-more');
      if (showMore && root.contains(showMore)) {
        showMore.dataset.expanded = 'true';
        applyFilters();
        return;
      }
      const dateFilter = event.target.closest('.activity-date-filter');
      if (dateDropdown && dateFilter === dateDropdown && !dateOption) {
        const nextOpen = !dateDropdown?.classList.contains('open');
        dateDropdown?.classList.toggle('open', nextOpen);
        root.closest('.card')?.classList.toggle('dropdown-open', nextOpen);
        dateTrigger.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
        return;
      }
    });

    if (searchInput) {
      searchInput.addEventListener('input', applyFilters);
    }

    applyFilters();
  });

  document.addEventListener('click', (event) => {
    document.querySelectorAll('[data-date-dropdown="true"].open').forEach((dropdown) => {
      if (!dropdown.contains(event.target)) {
        dropdown.classList.remove('open');
        dropdown.closest('.card')?.classList.remove('dropdown-open');
        const trigger = dropdown.querySelector('.activity-date-trigger');
        trigger?.setAttribute('aria-expanded', 'false');
      }
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-date-dropdown="true"].open').forEach((dropdown) => {
      dropdown.classList.remove('open');
      dropdown.closest('.card')?.classList.remove('dropdown-open');
      const trigger = dropdown.querySelector('.activity-date-trigger');
      trigger?.setAttribute('aria-expanded', 'false');
    });
  });
})();
