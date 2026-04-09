(() => {
        const storageKey = 'sprint-health-theme';
        const themeCheckbox = document.getElementById('themeCheckbox');
        const themeToggleText = document.getElementById('themeToggleText');

        function applyTheme(theme) {
          document.body.dataset.theme = theme;
          if (themeToggleText) themeToggleText.textContent = theme === 'light' ? 'Light' : 'Dark';
          if (themeCheckbox) themeCheckbox.checked = theme === 'light';
        }

        const savedTheme = localStorage.getItem(storageKey);
        const preferredTheme = savedTheme || 'dark';
        applyTheme(preferredTheme);

        themeCheckbox?.addEventListener('change', () => {
          const nextTheme = themeCheckbox.checked ? 'light' : 'dark';
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
                ink: 'var(--ant-n-800)',
                brand: 'var(--ant-primary-600)',
                muted: 'rgba(9, 88, 217, 0.45)',
                glow: 'rgba(22, 119, 255, 0.15)',
                coreA: 'rgba(255,255,255,0.9)',
                coreB: 'rgba(22,119,255,0.4)'
              }
              : {
                ink: 'var(--ant-n-200)',
                brand: 'var(--ant-primary-400)',
                muted: 'rgba(220, 233, 255, 0.25)',
                glow: 'rgba(76, 154, 255, 0.12)',
                coreA: 'rgba(255,255,255,0.28)',
                coreB: 'rgba(76,154,255,0.18)'
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
            centerY = Math.min(260, canvasHeight * 0.18);
            baseRadius = Math.min(canvasWidth, canvasHeight) * 0.10;
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
            const userDensity = 600;
            const count = canvasWidth < 900 ? Math.floor(userDensity / 2) : userDensity;
            particles.length = 0;
            for (let i = 0; i < count; i += 1) {
              const isIcon = i % 10 === 0;
              particles.push({
                x: Math.random() * canvasWidth,
                y: Math.random() * canvasHeight,
                vx: (Math.random() - 0.5) * 0.2,
                vy: (Math.random() - 0.5) * 0.2,
                angle: Math.random() * Math.PI * 2,
                angularVelocity: (Math.random() - 0.5) * 0.01,
                size: isIcon ? 10 + Math.random() * 8 : 1.8 + Math.random() * 3.5,
                alpha: isIcon ? 0.35 + Math.random() * 0.15 : 0.15 + Math.random() * 0.2,
                sprite: isIcon ? iconSprites[i % iconSprites.length] : null,
                color: i % 3 === 0 ? palette.brand : palette.muted,
                phase: Math.random() * Math.PI * 2
              });
            }
          }

          function drawGlow(pulse) {
            const palette = particlePalette();
            const gradient = particleCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 1.55);
            gradient.addColorStop(0, palette.glow);
            gradient.addColorStop(0.46, document.body.dataset.theme === 'light' ? 'rgba(0,82,204,0.015)' : 'rgba(76,154,255,0.02)');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');
            particleCtx.fillStyle = gradient;
            particleCtx.beginPath();
            particleCtx.arc(centerX, centerY, baseRadius * (1.02 + pulse * 0.03), 0, Math.PI * 2);
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

          function drawParticle(p, elapsed, pulse) {
            const time = elapsed * 0.001;
            const driftX = Math.sin(time + p.phase) * 15;
            const driftY = Math.cos(time * 0.8 + p.phase) * 20;

            let x = p.x + driftX;
            let y = p.y + driftY;

            // Wrap around screen
            if (x < -60) p.x = canvasWidth + 60;
            if (x > canvasWidth + 60) p.x = -60;
            if (y < -60) p.y = canvasHeight + 60;
            if (y > canvasHeight + 60) p.y = -60;

            particleCtx.save();
            particleCtx.translate(x, y);
            particleCtx.rotate(p.angle + elapsed * p.angularVelocity);
            particleCtx.globalAlpha = p.alpha * (0.85 + pulse * 0.15);

            if (p.sprite) {
              const size = p.size * (1 + pulse * 0.03);
              particleCtx.drawImage(p.sprite, -size / 2, -size / 2, size, size);
            } else {
              particleCtx.fillStyle = p.color;
              particleCtx.beginPath();
              particleCtx.arc(0, 0, p.size, 0, Math.PI * 2);
              particleCtx.fill();
            }

            particleCtx.restore();
          }

          function renderParticles(now) {
            particleCtx.clearRect(0, 0, canvasWidth, canvasHeight);
            orbitTime += 16.67;
            const pulse = 0.5 + Math.sin((now - startedAt) * 0.0024) * 0.5;
            for (let i = 0; i < particles.length; i += 1) {
              drawParticle(particles[i], orbitTime, pulse);
            }

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
      
        // Inject Theme Sync for Unified Console
        window.addEventListener('storage', (e) => {
          if (e.key === storageKey && typeof applyTheme === 'function') {
            applyTheme(e.newValue);
          }
        });
    
      })();