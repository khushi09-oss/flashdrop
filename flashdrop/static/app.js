const byId = (id) => document.getElementById(id);
const form = byId('order-form');
const place = byId('place');
const retry = byId('retry');
let lastRequest = null;
let busy = false;

function showStatus(state, message) {
  byId('status').textContent = state;
  byId('status').dataset.state = state;
  byId('message').textContent = message;
}

async function request(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(8000)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status}).`);
  return {body, replayed: response.headers.get('Idempotency-Replayed') === 'true'};
}

async function refreshStock() {
  try {
    const {body} = await request('/products/iphone_16');
    byId('stock').textContent = `${body.stock} units left`;
    byId('connection').textContent = 'Connected to your local backend';
  } catch {
    byId('stock').textContent = 'Stock unavailable';
    byId('connection').textContent = 'Backend unavailable';
  }
}

async function submitOrder(payload) {
  if (busy) return;
  busy = true;
  place.disabled = true;
  retry.disabled = true;
  byId('order-id').textContent = '—';
  byId('request-key').textContent = payload.idempotencyKey;
  byId('request-summary').textContent = `${payload.userId} · ${payload.quantity} unit(s)`;
  showStatus('SENDING', 'Sending your request…');
  try {
    const headers = {'Content-Type': 'application/json', 'X-User-Id': payload.userId};
    const accepted = await request('/orders', {method: 'POST', headers, body: JSON.stringify(payload)});
    byId('order-id').textContent = accepted.body.orderId;
    const result = {request: payload, acceptance: accepted.body, replayed: accepted.replayed};
    byId('response').textContent = JSON.stringify(result, null, 2);
    showStatus('PENDING', accepted.replayed ? 'Same order returned. No extra stock reserved.' : 'Stock reserved. Waiting for the worker…');
    await refreshStock();
    let confirmed = false;
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const {body} = await request(`/orders/${accepted.body.orderId}`, {headers});
      result.current = body;
      byId('response').textContent = JSON.stringify(result, null, 2);
      if (body.status === 'CONFIRMED') {
        confirmed = true;
        showStatus('CONFIRMED', accepted.replayed ? 'Retry verified: same order, no extra stock reserved.' : 'Your order was confirmed by the background worker.');
        break;
      }
    }
    if (!confirmed) showStatus('PENDING', 'Still waiting for the worker. Retry to check this same order again.');
  } catch (error) {
    showStatus('ERROR', `${error.message} Use “Retry last request” to safely retry the same purchase.`);
  } finally {
    busy = false;
    place.disabled = false;
    retry.disabled = lastRequest === null;
    await refreshStock();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (busy) return;
  lastRequest = {userId: byId('user').value, productId: 'iphone_16',
    quantity: Number(byId('quantity').value), idempotencyKey: crypto.randomUUID()};
  submitOrder(lastRequest);
});
retry.addEventListener('click', () => {
  if (lastRequest) submitOrder(lastRequest);
});
refreshStock();
