// Only bounded public ticker codes/names cross this bridge. Never API keys.
export default function ({ data, setStateValue, parentElement }) {
    const status = parentElement.querySelector('[role="status"]');
    if (parentElement.__request === data.request_id) return;
    parentElement.__request = data.request_id;
    const storageKey = 'predash.watchlist.v1';
    let result;
    try {
        if (data.operation === 'load') {
            const payload = localStorage.getItem(storageKey);
            // Bound even corrupted or manually edited browser storage.
            if (payload !== null && payload.length > 20000) throw new Error('size');
            result = { request_id: data.request_id, status: 'loaded', payload };
            status.textContent = '관심종목 목록을 불러왔습니다.';
        } else if (data.operation === 'save') {
            if (typeof data.payload !== 'string' || data.payload.length > 20000) throw new Error('size');
            localStorage.setItem(storageKey, data.payload);
            result = { request_id: data.request_id, status: 'saved' };
            status.textContent = '관심종목 · 이 브라우저에 저장 완료';
        }
    } catch (_) {
        result = { request_id: data.request_id, status: 'error' };
        status.textContent = '브라우저 저장을 사용할 수 없습니다. 목록을 백업하세요.';
    }
    if (result) setStateValue('result', result);
}
