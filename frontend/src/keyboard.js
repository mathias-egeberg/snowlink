/**
 * On-screen Norwegian QWERTY keyboard.
 *
 * Shows automatically when any text / number / password input is tapped.
 * Relies on touchstart (not just focusin) because Pi's Chromium kiosk may
 * not reliably fire focusin on touch.
 *
 * inputmode="none" is injected on all inputs so the OS virtual keyboard
 * stays hidden while ours is active.
 *
 * Key insight: touchstart+preventDefault on the keyboard container prevents
 * the active input from losing focus while the user types.
 */
const Keyboard = (() => {
  let _target  = null;   // currently targeted <input>
  let _shifted = false;
  let _sym     = false;

  // ── Layouts ─────────────────────────────────────────────────────────────

  const _ROWS = {
    default: [
      ['1','2','3','4','5','6','7','8','9','0','BKSP'],
      ['q','w','e','r','t','y','u','i','o','p','å'],
      ['a','s','d','f','g','h','j','k','l','ø','æ'],
      ['SHIFT','z','x','c','v','b','n','m','.','-','/'],
    ],
    shift: [
      ['!','"','#','$','%','&','/','(',')','=','BKSP'],
      ['Q','W','E','R','T','Y','U','I','O','P','Å'],
      ['A','S','D','F','G','H','J','K','L','Ø','Æ'],
      ['SHIFT','Z','X','C','V','B','N','M',',','_','+'],
    ],
    sym: [
      ['1','2','3','4','5','6','7','8','9','0','BKSP'],
      ['!','@','#','$','%','^','&','*','(',')','='],
      ['-','_','+','/','\\',':',';',"'",'~','`','"'],
      ['<','>','[',']','{','}','|','?',',','.','!'],
    ],
  };

  const _BOTTOM = {
    default: ['SYM', 'SPACE', 'DONE'],
    shift:   ['SYM', 'SPACE', 'DONE'],
    sym:     ['ABC', 'SPACE', 'DONE'],
  };

  const _LABEL = {
    BKSP:  '⌫',
    SHIFT: '⇧',
    SYM:   '!#1',
    ABC:   'ABC',
    SPACE: 'Space',
    DONE:  'Done ✓',
  };

  // ── DOM builder ──────────────────────────────────────────────────────────

  function _build() {
    const c = document.getElementById('osk-container');
    if (!c) return;
    c.innerHTML = '';

    const mode = _sym ? 'sym' : _shifted ? 'shift' : 'default';

    _ROWS[mode].forEach(row => {
      const rowEl = document.createElement('div');
      rowEl.className = 'osk-row';
      row.forEach(key => rowEl.appendChild(_makeKey(key)));
      c.appendChild(rowEl);
    });

    const botRow = document.createElement('div');
    botRow.className = 'osk-row osk-bottom-row';
    _BOTTOM[mode].forEach(key => botRow.appendChild(_makeKey(key)));
    c.appendChild(botRow);
  }

  function _makeKey(key) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'osk-key';
    btn.dataset.key = key;
    btn.textContent = _LABEL[key] ?? key;

    if (key === 'BKSP')                  btn.classList.add('osk-key-wide');
    if (key === 'SHIFT' && _shifted)      btn.classList.add('osk-key-active');
    if (key === 'SHIFT')                  btn.classList.add('osk-key-wide');
    if (key === 'SPACE')                  btn.classList.add('osk-key-space');
    if (key === 'DONE')                   btn.classList.add('osk-key-done');
    if (key === 'SYM' || key === 'ABC')   btn.classList.add('osk-key-sym');

    return btn;
  }

  // ── Key handling ─────────────────────────────────────────────────────────

  function _onKey(key) {
    if (!_target) return;

    switch (key) {
      case 'BKSP': {
        const s = _target.selectionStart ?? _target.value.length;
        const e = _target.selectionEnd   ?? _target.value.length;
        if (s !== e) {
          _insert('');
        } else if (s > 0) {
          _target.value = _target.value.slice(0, s - 1) + _target.value.slice(s);
          _target.selectionStart = _target.selectionEnd = s - 1;
          _target.dispatchEvent(new Event('input', { bubbles: true }));
        }
        break;
      }
      case 'DONE':
        _hide();
        break;
      case 'SPACE':
        _insert(' ');
        break;
      case 'SHIFT':
        _shifted = !_shifted;
        _build();
        break;
      case 'SYM':
        _sym = true; _shifted = false;
        _build();
        break;
      case 'ABC':
        _sym = false; _shifted = false;
        _build();
        break;
      default:
        _insert(key);
        if (_shifted && !_sym) { _shifted = false; _build(); }
    }
  }

  function _insert(text) {
    if (!_target) return;
    const s = _target.selectionStart ?? _target.value.length;
    const e = _target.selectionEnd   ?? _target.value.length;
    _target.value = _target.value.slice(0, s) + text + _target.value.slice(e);
    _target.selectionStart = _target.selectionEnd = s + text.length;
    _target.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // ── Show / hide ──────────────────────────────────────────────────────────

  function _show(input) {
    _target  = input;
    _shifted = false;
    _sym     = false;
    _build();
    document.getElementById('osk-container')?.classList.remove('hidden');
    // Scroll focused input into view once keyboard is painted
    requestAnimationFrame(() => {
      input.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  function _hide() {
    document.getElementById('osk-container')?.classList.add('hidden');
    _target = null;
  }

  // ── Input detection ──────────────────────────────────────────────────────

  function _isTextInput(el) {
    if (!el || !el.tagName) return false;
    if (el.tagName !== 'INPUT') return false;
    const t = (el.type || 'text').toLowerCase();
    return t === 'text' || t === 'number' || t === 'password' || t === 'email' || t === 'search';
  }

  // ── Initialisation ───────────────────────────────────────────────────────

  function _init() {
    const c = document.getElementById('osk-container');
    if (!c) return;

    // Suppress the OS virtual keyboard on all inputs; we provide our own.
    document.querySelectorAll('input').forEach(el => {
      el.setAttribute('inputmode', 'none');
      el.setAttribute('autocomplete', 'off');
      el.setAttribute('autocorrect', 'off');
      el.setAttribute('autocapitalize', 'off');
      el.setAttribute('spellcheck', 'false');
    });

    // Prevent the keyboard from blurring the active input on every touch.
    c.addEventListener('touchstart', e => e.preventDefault(), { passive: false });
    c.addEventListener('mousedown',  e => e.preventDefault());

    // Key dispatch — touchend for touch, click for mouse/pen.
    c.addEventListener('touchend', e => {
      const btn = e.target.closest('[data-key]');
      if (btn) { e.preventDefault(); _onKey(btn.dataset.key); }
    });
    c.addEventListener('click', e => {
      const btn = e.target.closest('[data-key]');
      if (btn) _onKey(btn.dataset.key);
    });

    // PRIMARY trigger: touchstart on an input → show keyboard immediately.
    // This fires before focusin and is the most reliable path on Pi.
    document.addEventListener('touchstart', e => {
      if (_isTextInput(e.target)) {
        e.target.focus();    // ensure focus so cursor is active
        if (_target !== e.target) _show(e.target);
      } else if (e.target.closest('#osk-container')) {
        // Touch inside keyboard — handled above; do nothing here.
      } else {
        // Tapped outside input and keyboard → hide.
        _hide();
      }
    }, { passive: true });

    // FALLBACK trigger: focusin fires for mouse/physical keyboard navigation.
    document.addEventListener('focusin', e => {
      if (_isTextInput(e.target) && _target !== e.target) _show(e.target);
    });

    // Hide when focus genuinely leaves all inputs (100 ms grace for field-hopping).
    document.addEventListener('focusout', () => {
      setTimeout(() => {
        if (!_isTextInput(document.activeElement)) _hide();
      }, 100);
    });
  }

  // Run after DOM is ready.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  return { hide: _hide };
})();
