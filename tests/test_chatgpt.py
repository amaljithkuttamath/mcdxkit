"""Independent OAuth boundary tests; synthetic credentials, no external requests."""
import hashlib
import base64
import json
import os
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from mcdxkit.chatgpt import ChatGPTAccounts, ChatGPTError, PLAN_SCOPE, ISSUER, DYNAMIC_CLIENT, loopback_origin


class SignInContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.accounts = ChatGPTAccounts(self.tmp.name)
        self.accounts._pending.clear(); self.accounts._starts.clear()
        network = patch('mcdxkit.chatgpt._call', side_effect=AssertionError('Unexpected network request'))
        self.network = network.start()
        self.addCleanup(network.stop)

    def begin(self, **kwargs):
        handle = self.accounts.begin('http://127.0.0.1:8765', **kwargs)
        url, binding = self.accounts.start(handle)
        params = {k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
        return params, binding

    def token_response(self, **extra):
        return httpx.Response(200, json={'access_token':'test-access', 'refresh_token':'test-refresh', 'token_type':'Bearer',
                                      'expires_in':3600, 'scope':PLAN_SCOPE, 'id_token':'test-id', **extra})

    def connect(self, client='oaiapp_test', subject='test-subject', scope=PLAN_SCOPE, rid=None):
        params, binding = self.begin(**({'rid':rid} if rid else {}))
        self.network.side_effect = None
        self.network.return_value = self.token_response(scope=scope)
        claims = {'iss':ISSUER, 'sub':subject, 'email':'example@example.test'}
        with patch('mcdxkit.chatgpt.verify_id_token', return_value=claims):
            outcome = self.accounts.complete({'state':params['state'],'code':'test-code','client_id':client}, binding)
        return outcome[0], self.accounts.registry()['active']

    def test_origin_is_exact_loopback_and_invalid_origins_fail_closed(self):
        self.assertEqual(loopback_origin('http://127.0.0.1:8765'), 'http://127.0.0.1:8765')
        for origin in ['https://127.0.0.1:8765','http://localhost:8765','http://127.0.0.1:8765#bad',
                       'http://u@127.0.0.1:8765','http://127.0.0.1:bad','http://[127.0.0.1:8765',
                       'http://127.0.0.1:8765?x=1','http://127.0.0.1:8765/path']:
            with self.subTest(origin=origin): self.assertIsNone(loopback_origin(origin))

    def test_fresh_pkce_nonce_state_exact_callback_and_persistent_host(self):
        first, _ = self.begin(); second, _ = self.begin()
        for k in ['nonce','state','code_challenge']: self.assertNotEqual(first[k], second[k])
        self.assertEqual(first['client_id'], DYNAMIC_CLIENT)
        self.assertEqual(first['redirect_uri'], 'http://127.0.0.1:8765/auth/callback')
        self.assertEqual(first['code_challenge_method'], 'S256')
        self.assertEqual(first['ext_agent_host_id'], second['ext_agent_host_id'])
        self.assertEqual(first['ext_agent_host_id'], ChatGPTAccounts(self.tmp.name).host_id())
        self.assertNotIn('client_secret', first)

    def test_wrong_state_cookie_and_decline_cannot_exchange(self):
        for kind in ['state','cookie','decline']:
            params, binding = self.begin()
            q={'state':params['state'], 'code':'test-code','client_id':'oaiapp_test'}
            if kind=='state': q['state']='wrong'
            if kind=='cookie': binding='wrong'
            if kind=='decline': q['error']='access_denied'
            self.assertEqual(self.accounts.complete(q,binding)[0], 'failed')
        self.network.assert_not_called()
        self.assertEqual(self.accounts.status()['accounts'],[])

    def test_valid_exchange_uses_issued_client_and_private_atomic_storage(self):
        outcome, rid = self.connect()
        self.assertEqual(outcome,'connected')
        data=self.network.call_args.kwargs['data']
        self.assertEqual(data['client_id'],'oaiapp_test')
        self.assertEqual(data['redirect_uri'],'http://127.0.0.1:8765/auth/callback')
        self.assertNotIn('client_secret',data)
        for p in self.accounts.dir.glob('tokens-*.json'):
            if os.name!='nt': self.assertEqual(p.stat().st_mode&0o777,0o600)
        status=json.dumps(self.accounts.status())
        for secret in ['test-access','test-refresh','test-id','test-code']: self.assertNotIn(secret,status)
        self.assertEqual(self.accounts.tokens(rid)['subject'],'test-subject')

    def test_nonce_and_pkce_bound_to_original_attempt_and_callback_is_one_time(self):
        params,binding=self.begin()
        self.network.side_effect=None;self.network.return_value=self.token_response()
        claims={'iss':ISSUER,'sub':'subject'}
        with patch('mcdxkit.chatgpt.verify_id_token',return_value=claims) as verify:
            query={'state':params['state'],'code':'test-code','client_id':'oaiapp_test'}
            self.accounts.complete(query,binding)
            verify.assert_called_once_with('test-id','oaiapp_test',params['nonce'])
            verifier=self.network.call_args.kwargs['data']['code_verifier']
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.assertEqual(challenge,params['code_challenge'])
            self.assertEqual(self.accounts.complete(query,binding)[0], 'failed')
        self.assertEqual(self.network.call_count,1)

    def test_identity_only_consent_does_not_enable_model_use(self):
        outcome,rid=self.connect(scope='openid email')
        self.assertEqual(outcome,'plan_not_enabled')
        with self.assertRaises(ChatGPTError):self.accounts.access_token(rid)

    def test_returning_client_or_subject_cannot_replace_existing_account(self):
        _,rid=self.connect()
        old=self.accounts.tokens(rid)
        params,binding=self.begin(rid=rid)
        self.assertEqual(params['client_id'],'oaiapp_test')
        self.assertEqual(self.accounts.complete({'state':params['state'],'code':'new-code','client_id':'oaiapp_other'},binding)[0], 'failed')
        self.assertEqual(self.connect(subject='different-subject',rid=rid)[0], 'failed')
        self.assertEqual(self.accounts.tokens(rid),old)

    def test_same_email_different_registration_is_separate_account(self):
        _,first=self.connect(client='oaiapp_one')
        _,second=self.connect(client='oaiapp_two')
        self.assertNotEqual(first,second)
        self.assertEqual(len(self.accounts.status()['accounts']),2)

    def expired(self):
        _,rid=self.connect()
        old=self.accounts.tokens(rid);old['expires_at']=time.time()-1
        self.accounts._write('tokens-'+rid+'.json',old)
        self.network.reset_mock()
        return rid,old

    def test_refresh_scope_loss_blocks_use_immediately(self):
        rid,_=self.expired()
        self.network.return_value=self.token_response(scope='openid email')
        with self.assertRaises(ChatGPTError):self.accounts.access_token(rid)
        self.assertNotIn(PLAN_SCOPE,self.accounts.tokens(rid)['scopes'])

    def test_refresh_preserves_omitted_scope_and_rotates_credentials_together(self):
        rid,_=self.expired()
        payload=self.token_response(access_token='rotated-access',refresh_token='rotated-refresh').json();del payload['scope']
        self.network.return_value=httpx.Response(200,json=payload)
        self.assertEqual(self.accounts.access_token(rid),'rotated-access')
        self.assertEqual(self.accounts.tokens(rid)['refresh_token'],'rotated-refresh')
        self.assertIn(PLAN_SCOPE,self.accounts.tokens(rid)['scopes'])
        self.assertNotIn('scope',self.network.call_args.kwargs['data'])

    def test_terminal_refresh_clears_credentials_temporary_failure_preserves_them(self):
        rid,old=self.expired()
        self.network.return_value=httpx.Response(503,json={'error':'temporarily_unavailable'})
        with self.assertRaises(ChatGPTError):self.accounts.access_token(rid)
        self.assertEqual(self.accounts.tokens(rid),old)
        self.network.return_value=httpx.Response(400,json={'error':'invalid_grant'})
        with self.assertRaises(ChatGPTError):self.accounts.access_token(rid)
        self.assertIsNone(self.accounts.tokens(rid))
        self.assertEqual(self.accounts.account(rid)['client_id'],'oaiapp_test')

    def test_concurrent_refresh_rotates_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        rid,_=self.expired()
        def reply(*args,**kwargs):
            time.sleep(0.05)
            return self.token_response(access_token='rotated-once')
        self.network.side_effect=reply
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(ChatGPTAccounts(self.tmp.name).access_token,rid) for _ in range(2)]
            self.assertEqual([f.result(timeout=5) for f in futures],['rotated-once','rotated-once'])
        self.assertEqual(self.network.call_count,1)

    def test_signout_disables_locally_even_when_remote_revocation_fails(self):
        _,rid=self.connect()
        with patch('mcdxkit.chatgpt.discovery',return_value={'revocation_endpoint':ISSUER+'/revoke'}):
            def revoke(*args,**kwargs):
                self.assertIsNone(self.accounts.tokens(rid),'Local account must be disabled before network revocation')
                return httpx.Response(400,json={'error':'declined'})
            self.network.side_effect=revoke
            result=self.accounts.disconnect(rid)
        self.assertFalse(result['revoked']);self.assertIsNotNone(result['message'])
        self.assertIsNone(self.accounts.tokens(rid))
        self.assertEqual(self.accounts.account(rid)['client_id'],'oaiapp_test')
        with self.assertRaises(ChatGPTError):self.accounts.access_token(rid)

    def test_catalog_filters_hidden_models_preserves_order_and_hides_credentials(self):
        _,rid=self.connect()
        self.network.return_value=httpx.Response(200,json={'models':[
            {'slug':'model-b','display_name':'Model B','visibility':'list'},
            {'slug':'hidden','visibility':'hidden'},
            {'slug':'model-a','display_name':'Model A','visibility':'list'}]})
        result=self.accounts.models(rid)
        self.assertEqual([m['slug'] for m in result['models']],['model-b','model-a'])
        self.assertNotIn('test-access',json.dumps(result))



class SignatureContract(unittest.TestCase):
    def test_real_signed_tokens_reject_signature_identity_expiry_and_nonce_tampering(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from mcdxkit.chatgpt import verify_id_token
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='test-key',alg='RS256')
        keys=jwt.PyJWKSet.from_dict({'keys':[jwk]})
        base={'iss':ISSUER,'sub':'test-subject','aud':'oaiapp_test','iat':int(time.time()),'exp':int(time.time())+600,'nonce':'test-nonce'}
        def sign(claims, signing_key=key):return jwt.encode(claims,signing_key,algorithm='RS256',headers={'kid':'test-key'})
        with patch('mcdxkit.chatgpt._signing_keys',return_value=keys):
            self.assertEqual(verify_id_token(sign(base),'oaiapp_test','test-nonce')['sub'],'test-subject')
            for changes in [{'iss':'https://evil.test'},{'aud':'other-client'},{'exp':1},{'nonce':'wrong'},{'sub':''}]:
                with self.subTest(changes=changes),self.assertRaises(ChatGPTError):
                    verify_id_token(sign({**base,**changes}),'oaiapp_test','test-nonce')
            other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            with self.assertRaises(ChatGPTError):verify_id_token(sign(base,other),'oaiapp_test','test-nonce')

    def test_discovery_does_not_allow_foreign_jwks_or_issuer(self):
        from mcdxkit.chatgpt import discovery
        for value in [{'issuer':ISSUER,'jwks_uri':'https://evil.test/keys'},
                      {'issuer':'https://evil.test','jwks_uri':ISSUER+'/.well-known/jwks.json'}]:
            with patch('mcdxkit.chatgpt._cache',{}),patch('mcdxkit.chatgpt._call',return_value=httpx.Response(200,json=value)):
                with self.assertRaises(ChatGPTError):discovery()

    def test_shared_server_flag_blocks_before_credentials_or_network(self):
        from mcdxkit.chatgpt import DISABLED_ENV
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{DISABLED_ENV:'1'}),patch('mcdxkit.chatgpt._call') as network:
            accounts=ChatGPTAccounts(folder)
            with self.assertRaises(ChatGPTError):accounts.begin('http://127.0.0.1:8765')
            with self.assertRaises(ChatGPTError):accounts.access_token('0000000000000000')
            network.assert_not_called()


if __name__=='__main__':unittest.main()
