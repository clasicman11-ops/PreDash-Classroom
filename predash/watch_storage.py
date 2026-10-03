"""Restore and acknowledge a browser-local watchlist, never account data."""
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import streamlit as st
from streamlit.components.v2 import component

from predash.watchlist import clean_codes, clean_names, export_backup, restore_backup, backup_names

browser_store = component(
    'predash_watch_storage',
    html='<span role="status" aria-live="polite">관심종목 저장 목록 확인 중…</span>',
    css='span {font-size: .8rem; color: var(--st-text-color); opacity: .7;}',
    js=Path(__file__).with_suffix('.js').read_text(encoding='utf-8'),
)


def sync_watchlist():
    """Load before any edits; save each changed list and wait for browser ack."""
    state = st.session_state
    if 'watch_storage_nonce' not in state:
        state.watch_storage_nonce = uuid4().hex
    ready = state.get('watch_storage_ready', False)
    payload = export_backup(state.get('watch_codes', []), state.get('watch_names', {})) if ready else None
    operation = 'save' if ready else 'load'
    request_id = state.watch_storage_nonce + ':' + (sha256(payload.encode()).hexdigest() if ready else 'load')
    response = browser_store(
        data=dict(operation=operation, request_id=request_id, payload=payload),
        key='watch_storage_component', on_result_change=lambda: None,
    ).get('result')
    if not isinstance(response, dict) or response.get('request_id') != request_id:
        state.watch_storage_status = 'saving' if ready else 'loading'
        return ready
    status = response.get('status')
    if not ready and status in ('loaded', 'error'):
        saved_codes, saved_names = [], {}
        stored = response.get('payload')
        if stored is not None:
            try:
                if not isinstance(stored, str) or len(stored) > 20000:
                    raise ValueError
                saved_codes, saved_names = restore_backup(stored), backup_names(stored)
            except ValueError:
                # Leave corrupted data untouched until the user chooses recovery.
                state.watch_storage_status = 'invalid'
                return False
        if 'watch_codes' not in state:
            state.watch_codes = (clean_codes(st.query_params.get('watch', '').split(','))
                                 if 'watch' in st.query_params else saved_codes)
        state.watch_names = clean_names({**saved_names, **state.get('watch_names', {})}, state.watch_codes)
        state.watch_storage_ready = True
        state.watch_storage_status = 'error' if status == 'error' else 'saving'
        st.rerun()
    state.watch_storage_status = 'saved' if status == 'saved' else 'error'
    return ready


def watch_storage_notice():
    """Show a truthful save state and recover without trapping the rest of the app."""
    status = st.session_state.get('watch_storage_status')
    if status in ('loading', 'invalid'):
        if status == 'invalid':
            st.warning('브라우저의 저장 목록을 읽지 못했습니다. 백업 파일을 준비한 후 새 목록으로 시작하거나 복원하세요.')
        else:
            st.info('브라우저의 관심종목을 불러오는 중입니다.')
        st.caption('새 목록으로 시작하면 이 브라우저의 기존 목록을 교체합니다. 이후 백업 파일을 복원할 수 있습니다.')
        if st.button('저장 목록 대신 새 목록으로 시작'):
            st.session_state.watch_codes = clean_codes(st.query_params.get('watch', '').split(','))
            st.session_state.watch_names = {}
            st.session_state.watch_storage_ready = True
            st.rerun()
        return False
    if status == 'error':
        st.warning('브라우저 저장이 차단되어 현재 접속에서만 유지됩니다. 종료 전 종목 목록을 백업하세요.')
    elif status == 'saving':
        st.caption('브라우저에 목록을 저장하는 중입니다. 저장 완료 표시 후 화면을 닫으세요.')
    else:
        st.caption('관심종목 코드·이름은 이 브라우저에 자동 저장됩니다. 다른 기기는 백업·복원으로 옮기세요.')
    return True
