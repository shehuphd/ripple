/* Shared helpers. */
function toast(message, isError) {
  const node = document.createElement('div');
  node.className = 'toast' + (isError ? ' err' : '');
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.message || body.detail || `Request failed (${response.status})`);
  }
  return body;
}

function form(pairs) {
  const data = new FormData();
  for (const [key, value] of Object.entries(pairs)) {
    if (value !== null && value !== undefined) data.append(key, value);
  }
  return data;
}
