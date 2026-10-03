"""Session-isolated broker clients with optional owner-managed Secrets."""
from ipaddress import IPv4Address

import requests
import streamlit as st

from predash.kiwoom import Kiwoom, BrokerError


def initialize_saved_connections():
    """Load complete saved profiles once, only after password authentication."""
    if not st.session_state.get('authorized') or st.session_state.get('kiwoom_saved_loaded'):
        return
    credentials = dict(st.session_state.get('kiwoom_credentials', {}))
    default = 'demo'
    try:
        for mode in ('real', 'demo'):
            prefix = 'KIWOOM_' + mode.upper()
            key = str(st.secrets.get(prefix + '_APP_KEY', '')).strip()
            secret = str(st.secrets.get(prefix + '_APP_SECRET', '')).strip()
            if key and secret and mode not in credentials:
                credentials[mode] = dict(mode=mode, key=key, secret=secret, source='secrets')
        default = str(st.secrets.get('KIWOOM_DEFAULT_MODE', 'demo')).strip()
    except FileNotFoundError:
        pass
    st.session_state.kiwoom_credentials = credentials
    if credentials and st.session_state.get('kiwoom_active_mode') not in credentials:
        st.session_state.kiwoom_active_mode = default if default in credentials else next(iter(credentials))
    st.session_state.kiwoom_saved_loaded = True


def account_settings(mode=None):
    credentials = st.session_state.get('kiwoom_credentials', {})
    selected = mode or st.session_state.get('kiwoom_active_mode', 'demo')
    saved = credentials.get(selected, {})
    return dict(mode=selected, key=saved.get('key', ''), secret=saved.get('secret', ''))


def broker_client(mode=None):
    settings = account_settings(mode)
    cache_key = '_kiwoom_client_' + settings['mode']
    client = st.session_state.get(cache_key)
    if client is None or not isinstance(client, Kiwoom) or any(
            getattr(client, key) != settings[key] for key in ('mode', 'key', 'secret')):
        client = Kiwoom(settings=settings)
        st.session_state[cache_key] = client
    return client


def clear_account_views():
    """Never show cached results from another broker, key or investment mode."""
    keep = {'authorized', 'navigation', 'watch_codes', 'watch_names', '_broker_schema',
            'classroom_api_keys', 'kiwoom_saved_loaded',
            'kiwoom_credentials', 'kiwoom_active_mode', '_kiwoom_client_real', '_kiwoom_client_demo'}
    for key in list(st.session_state):
        if key not in keep and not key.startswith('watch_storage_'):
            del st.session_state[key]


def server_ipv4():
    """Only called by the IP-check button; sends no broker credentials."""
    try:
        response = requests.get('https://api.ipify.org', params={'format': 'json'}, timeout=(5, 10))
        response.raise_for_status()
        address = IPv4Address(response.json()['ip'])
        if not address.is_global:
            raise ValueError
        return str(address)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise BrokerError('앱 서버 IP 확인에 실패했습니다. 호스팅 관리자 화면에서 발신 IP를 확인하세요.') from None


def connection_form():
    st.subheader('내 키움증권 계좌 연결')
    st.caption('키움 REST API App Key·App Secret을 사용합니다. Windows OpenAPI+ 방식은 지원하지 않습니다.')
    st.info('키움에 등록할 허용 IP는 이 앱 서버의 IP입니다. 계좌는 발급된 App Key에 연결되므로 계좌번호를 따로 입력하지 않습니다.')
    if st.button('앱 서버 IP 확인'):
        try:
            st.session_state.kiwoom_server_ip = server_ipv4()
        except BrokerError as error:
            st.error(str(error))
    if st.session_state.get('kiwoom_server_ip'):
        st.code(st.session_state.kiwoom_server_ip, language=None)
    st.caption('IP 확인은 외부 서비스(ipify)를 이용하며 키를 보내지 않습니다. Streamlit Cloud의 IP는 바뀔 수 있으므로 재시작 후 연결 오류가 나면 다시 확인하세요.')
    credentials = st.session_state.get('kiwoom_credentials', {})
    modes = [m for m in ('real', 'demo') if credentials.get(m)]
    labels = {'real': '실전 조회', 'demo': '모의투자 조회'}
    for mode in modes:
        if credentials[mode].get('source') == 'secrets':
            st.info('키움 ' + labels[mode] + ' · Secrets 설정 불러옴 · 조회 시 인증')
        else:
            st.success('키움 ' + labels[mode] + ' 연결됨')
        if st.button(labels[mode] + ' 연결 해제', key='disconnect_' + mode):
            remaining = {m: s for m, s in credentials.items() if m != mode}
            st.session_state.kiwoom_credentials = remaining
            st.session_state.pop('_kiwoom_client_' + mode, None)
            if st.session_state.get('kiwoom_active_mode') == mode:
                st.session_state.kiwoom_active_mode = next(iter(remaining), 'demo')
            clear_account_views()
            st.rerun()
    if modes:
        current = st.session_state.get('kiwoom_active_mode', modes[0])
        index = modes.index(current) if current in modes else 0
        selected = st.radio('일반 화면에서 사용할 계좌', modes, index=index,
                            format_func=lambda m: labels[m], key='kiwoom_active_choice')
        if selected != current:
            st.session_state.kiwoom_active_mode = selected
            clear_account_views()
            st.rerun()
    available = [m for m in ('demo', 'real') if m not in modes]
    if available:
        with st.form('kiwoom_connection'):
            mode = st.radio('연결할 환경', available, format_func=lambda m: labels[m], horizontal=True)
            key = st.text_input('키움 App Key', type='password', key='kiwoom_input_key')
            secret = st.text_input('키움 App Secret', type='password', key='kiwoom_input_secret')
            submitted = st.form_submit_button('키움 연결 확인', type='primary', use_container_width=True)
        if submitted:
            settings = dict(mode=mode, key=key.strip(), secret=secret.strip())
            try:
                client = Kiwoom(settings=settings)
                with st.spinner('키움 잔고 조회 권한을 확인합니다…'):
                    snapshot = client.balance()
                st.session_state.kiwoom_credentials = {**credentials, mode: settings}
                st.session_state['_kiwoom_client_' + mode] = client
                st.session_state.kiwoom_active_mode = mode
                clear_account_views()
                if snapshot.get('cash_error'):
                    st.session_state.kiwoom_cash_notice = snapshot['cash_error']
                st.rerun()
            except BrokerError as error:
                st.error(str(error))
    if st.session_state.get('kiwoom_cash_notice'):
        st.warning('잔고 연결은 성공했지만 예수금은 조회 보류입니다. ' + st.session_state.kiwoom_cash_notice)
    st.link_button('키움 REST API · 키 및 허용 IP 관리', 'https://openapi.kiwoom.com/intro/serviceInfo')
    st.caption('직접 입력한 키는 현재 접속에서만 사용합니다. Secrets에 저장한 키는 다음 로그인에서 다시 불러옵니다. 연결 해제는 현재 접속에만 적용되며, 영구 삭제는 Streamlit Settings → Secrets에서 합니다. 연결 변경·해제 시 조회 결과와 실습 기록이 지워지므로 먼저 백업하세요.')
