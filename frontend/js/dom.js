/**
 * Помощники для сборки хтмл и свг
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

function applyAttributes(node, attributes) {
  for (const [key, value] of Object.entries(attributes || {})) {
    if (value === null || value === undefined || value === false) continue;

    if (key === 'text') {
      node.textContent = value;
    } else if (key === 'html') {
      node.innerHTML = value;
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      node.setAttribute(key, value);
    }
  }
}

function appendChildren(node, children) {
  for (const child of children || []) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}

export function el(tag, attributes, children) {
  const node = document.createElement(tag);
  applyAttributes(node, attributes);
  appendChildren(node, children);
  return node;
}

export function svg(tag, attributes, children) {
  const node = document.createElementNS(SVG_NS, tag);
  applyAttributes(node, attributes);
  appendChildren(node, children);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function byId(id) {
  const node = document.getElementById(id);
  if (!node) throw new Error(`В разметке нет элемента #${id}`);
  return node;
}

export function show(node, visible) {
  node.classList.toggle('hidden', !visible);
}

export function fillSelect(select, options, selectedValue) {
  const previous = selectedValue ?? select.value;
  clear(select);
  for (const option of options) {
    select.appendChild(el('option', { value: option.value, text: option.label }));
  }
  if (options.some((option) => option.value === previous)) select.value = previous;
}
