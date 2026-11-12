"""Builds a local stand-in for the POWR substitution form from the saved configuration.

It mimics what matters to the filler: an open shadow root on <powr-form-builder>,
labelled native selects, fields shown only when their condition is met, a Next button
that validates page 1, and a Submit button that shows a thank-you message.
"""

import json


def build(components: list[dict]) -> str:
    return """<!doctype html><html><body>
<powr-form-builder></powr-form-builder>
<script>
const COMPS = %s;
const host = document.querySelector('powr-form-builder');
const root = host.attachShadow({mode: 'open'});
const step = COMPS.findIndex(c => c.type === 'step');
const pages = [COMPS.slice(0, step), COMPS.slice(step + 1)];
let html = '<form><div id="p1">' +
  '<div class="f"><label for="fname">Full Name*</label><input id="fname" type="text" required></div>';
const render = c => {
  if (c.type === 'email') return `<div class="f" data-idx="${c.idx}"><label for="${c.idx}">${c.label}*</label><input id="${c.idx}" type="email" required></div>`;
  if (c.type === 'select') return `<div class="f" data-idx="${c.idx}"><label for="${c.idx}">${c.label}*</label><select id="${c.idx}"><option value=""></option>` +
      c.options.filter(Boolean).map(o => `<option>${o}</option>`).join('') + '</select></div>';
  return '';
};
html += pages[0].map(render).join('') + '<p>1 of 2 pages</p><button type="button" id="next">Next</button></div>';
html += '<div id="p2" hidden>' + pages[1].map(render).join('') +
        '<p>2 of 2 pages</p><button type="button">Back</button><button type="button" id="submit">Submit</button></div></form>';
root.innerHTML = html;
const byIdx = Object.fromEntries(COMPS.map(c => [c.idx, c]));
const refresh = () => root.querySelectorAll('.f[data-idx]').forEach(div => {
  const c = byIdx[div.dataset.idx];
  if (!c.conditionalElement) return;
  const parent = root.getElementById(c.conditionalElement);
  const parentShown = parent && !parent.closest('.f').hidden;
  div.hidden = !(parentShown && parent.value === c.conditionalValue);
});
root.addEventListener('change', refresh); refresh();
const visibleEmpty = page => [...root.querySelectorAll(`#${page} input, #${page} select`)]
  .filter(e => !e.closest('.f').hidden && !e.value);
root.getElementById('next').onclick = () => {
  if (visibleEmpty('p1').length) return;
  root.getElementById('p1').hidden = true; root.getElementById('p2').hidden = false;
};
root.getElementById('submit').onclick = () => {
  if (visibleEmpty('p2').length) return;
  root.innerHTML = '<p>Thank you! Your submission has been received.</p>';
};
</script></body></html>""" % json.dumps(components)
