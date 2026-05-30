/**
 * On-screen Norwegian QWERTY keyboard.
 *
 * Shows automatically when any text / number / password input is focused.
 * Pressing keys inserts at the cursor; backspace deletes; Done dismisses.
 *
 * Touch trick: touchstart on the keyboard container calls preventDefault()
 * so the active input never loses focus while you type.
 */
const Keyboard = (() => {
  let _target  = null;   // currently focused <input>
  let _shifted = false;
  let _sym     = false;

  // ── Layouts ────────────────────────────────────────────────────────
  // Each inner array is one row. Last element of every row uses a
  // special key name understood by _onKey().

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

  // Bottom row is rendered separately (flexible widths)
  const _BOTTOM = {
    default: ['SYM','SPACE','DONE'],
    shift:   ['SYM','SPACE','DONE'],
    sym:     ['ABC','SPACE','DONE'],
  };

  // Labels shown on special keys
  const _LABEL = {
    BKSP:  '⌫',
    SHIFT: '⇧',
    SYM:   '!#1',
    ABC:   'ABC',
    SPACE: 'Space',
    DONE:  'Done',
  };

  // ── Build DOM ──────────────────────────────────────────────────────

  function _build() {
    const container = document.getElementById('osk-container');
    container.innerHTML = '';

    const mode = _sym ? 'sym' : _shifted ? 'shift' : 'default';

    // Main rows
    _ROWS[mode].forEach(row => {
      const rowEl = document.createElement('div');
      rowEl.className = 'osk-row';
      row.forEach(key => {
        rowEl.appendChild(_makeKey(key));
      });
      container.appendChild(rowEl);
    });

    // Bottom row
    const botRow = document.createElement('div');
    botRow.className = 'osk-row osk-bottom-row';
    _BOTTOM[mode].forEach(key => {
      botRow.appendChild(_makeKey(key));
    });
    container.appendChild(botRow);
  }

  function _makeKey(key) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'osk-key';
    btn.dataset.key = key;
    btn.textContent = _LABEL[key] ?? key;

    if (key === 'BKSP')  btn.classList.add('osk-key-wide');
    if (key === 'SHIFT')  btn.classList.add('osk-key-wide', _shifted ? 'osk-key-active' : '');
    if (key === 'SPACE')  btn.classList.add('osk-key-space');
    if (key === 'DONE')   btn.classList.add('osk-key-done');
    if (key === 'SYM' || key === 'ABC') btn.classList.add('osk-key-sym');

    return btn;
  }

  // ── Key handler ────────────────────────────────────────────────────

  function _onKey(key) {
    if (!_target) return;

    switch (key) {
      case 'BKSP': {
        const s = _target.selectionStart;
        const e = _target.selectionEnd;
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
        _target.blur();
        break;
      case 'SPACE':
        _insert(' ');
        break;
      case 'SHIFT':
        _shifted = !_shifted;
        _build();
        break;
      case 'SYM':
        _sym = true;
        _shifted = false;
        _build();
        break;
      case 'ABC':
        _sym = false;
        _shifted = false;
        _build();
        break;
      default:
        _insert(key);
        // Auto-unshift after one character (like a real keyboard)
        if (_shifted && !_sym) {
          _shifted = false;
          _build();
        }
    }
  }

  function _insert(text) {
    if (!_target) return;
    const s = _target.selectionStart;
    const e = _target.selectionEnd;
    _target.value = _target.value.slice(0, s) + text + _target.value.slice(e);
    _target.selectionStart = _target.selectionEnd = s + text.length;
    _target.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // ── Show / hide ────────────────────────────────────────────────────

  function _show(input) {
    _target  = input;
    _shifted = false;
    _sym     = false;
    _build();

    const c = document.getElementById('osk-container');
    c.classList.remove('hidden');

    // Scroll the focused input into view above the keyboard after it renders
    requestAnimationFrame(() => {
      input.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  function _hide() {
    _target = null;
    document.getElementById('osk-container')?.classList.add('hidden');
  }

  // ── Event wiring ───────────────────────────────────────────────────

  // Prevent the keyboard from stealing focus from the active input.
  // Using capture on the container covers both touch and mouse.
  function _initContainer() {
    const c = document.getElementById('osk-container');
    if (!c) return;

    // Prevent focus loss on touch
    c.addEventListener('touchstart', e => e.preventDefault(), { passive: false });
    c.addEventListener('mousedown',  e => e.preventDefault());

    // Route key presses (event delegation)
    c.addEventListener('touchend', e => {
      const btn = e.target.closest('[data-key]');
      if (btn) { e.preventDefault(); _onKey(btn.dataset.key); }
    });
    c.addEventListener('click', e => {
      const btn = e.target.closest('[data-key]');
      if (btn) _onKey(btn.dataset.key);
    });
  }

  // Show keyboard when a text/number/password input is focused.
  document.addEventListener('focusin', e => {
    const el = e.target;
    if (el.matches('input[type="text"], input[type="number"], input[type="password"], input:not([type])')) {
      _show(el);
    }
  });

  // Hide when focus moves completely outside inputs (e.g. button tap).
  document.addEventListener('focusout', () => {
    // Delay so a focusin on another input fires first.
    setTimeout(() => {
      const active = document.activeElement;
      if (!active || !active.matches('input[type="text"], input[type="number"], input[type="password"], input:not([type])')) {
        _hide();
      }
    }, 80);
  });

  // Boot: wire the container once DOM is ready.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initContainer);
  } else {
    _initContainer();
  }

  return { hide: _hide };
})();
