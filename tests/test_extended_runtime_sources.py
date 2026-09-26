"""Synthetic contracts only: never bundle real conversations or credentials."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src/dashboard"))

from data_foundation.runtime_sources.zcode import ZCodeRuntime
from data_foundation.runtime_sources.transcripts import QwenCodeRuntime, CopilotCliRuntime, ClineRuntime, ContinueRuntime, AiderRuntime, GrokBotRuntime
from data_foundation.runtime_sources.antigravity import AntigravityRuntime
from data_foundation.runtime_sources.registry import NEW_RUNTIME_IDS, build_runtime
from data_foundation.runtime_sources.files import MAX_FILE_BYTES, read_text
from data_foundation.adapters.usage import LocalRuntimeAdapter, default_usage_adapters
from data_foundation.settings import default_external_tool_settings, write_settings
from data_foundation.paths import initialize_home
from data_foundation.ingest import run_shadow_ingestion
from data_foundation.db import connect
from data_foundation.aggregate import daily_diary_usage_metrics
from data_foundation.time import business_date_for
from app.services import runtime_sources as service
from app.routers import dashboard_summary as routes
from ai_assets_center import unified_source_collector as collector

STAMP = "2026-09-25T12:00:00+00:00"
MS = int(datetime.fromisoformat(STAMP).timestamp() * 1000)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding="utf-8")


def zcode_fixture(home):
    p = home / "cli/db/db.sqlite"
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.executescript('''CREATE TABLE session(id TEXT, directory TEXT, title TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER);
        CREATE TABLE message(id TEXT, data TEXT);
        CREATE TABLE part(id TEXT, session_id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE model_usage(id TEXT, session_id TEXT, model_id TEXT, started_at INTEGER, completed_at INTEGER,
            input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, cache_creation_input_tokens INTEGER,
            cache_read_input_tokens INTEGER, provider_total_tokens INTEGER, status TEXT);
        CREATE TABLE todo(session_id TEXT, content TEXT, status TEXT, position INTEGER, time_updated INTEGER);
        CREATE TABLE turn_usage(input_tokens INTEGER);
        CREATE TABLE credentials(secret TEXT);''')
    c.execute('INSERT INTO session VALUES(?,?,?,?,?,?)', ('one','/workspace/demo','Verify restored files',None,MS,MS))
    for key, role, synthetic in [('u','user',False),('a','assistant',False),('s','user',True)]:
        c.execute('INSERT INTO message VALUES(?,?)', (key,json.dumps({'role':role,'synthetic':synthetic,'tokens':{'input':99999}})))
        c.execute('INSERT INTO part VALUES(?,?,?,?,?)', (key,'one',key,MS,json.dumps({'type':'text','text':'question' if key=='u' else 'verified answer' if key=='a' else 'hidden injected instruction'})))
    c.execute('INSERT INTO part VALUES(?,?,?,?,?)', ('thinking','one','a',MS,json.dumps({'type':'reasoning','text':'private reasoning'})))
    c.execute('INSERT INTO model_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('call-one','one','test-model',MS,MS,10,5,2,4,3,22,'completed'))
    c.execute('INSERT INTO turn_usage VALUES(99999)')
    c.execute('INSERT INTO todo VALUES(?,?,?,?,?)', ('one','Compare SHA-256 checksums','completed',0,MS))
    c.execute("INSERT INTO credentials VALUES('never-export-this-secret')")
    c.commit();c.close()
    return p


class ExtendedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        service._cache.clear()

    def tearDown(self):
        service._cache.clear()
        self.tmp.cleanup()

    def test_zcode_read_only_exactly_one_usage_authority_and_safe_text(self):
        home=self.root/'zcode'; p=zcode_fixture(home)
        before=hashlib.sha256(p.read_bytes()).hexdigest()
        r=ZCodeRuntime(home)
        self.assertEqual(len(r.sessions()),1)
        self.assertEqual([m.role for m in r.dialogue()],['assistant','user'])
        self.assertEqual(len(r.usage()),1)
        self.assertEqual((r.usage()[0].input_tokens,r.usage()[0].output_tokens,r.usage()[0].cache_read_tokens),(10,5,3))
        self.assertEqual(r.documents()[0].kind,'task-checklist')
        self.assertIn('not independently verified',r.documents()[0].content)
        exported=repr((r.dialogue(),r.documents(),r.usage()))
        for secret in ['private reasoning','hidden injected instruction','never-export-this-secret']:
            self.assertNotIn(secret,exported)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)

    def test_zcode_schema_drift_and_symlink_fail_closed(self):
        home=self.root/'zcode';p=zcode_fixture(home)
        c=sqlite3.connect(p);c.execute('DROP TABLE message');c.commit();c.close()
        r=ZCodeRuntime(home)
        self.assertEqual(r.dialogue(),())
        self.assertEqual(r.diagnostics[0]['code'],'unsupported-schema')
        link=self.root/'linked.sqlite';link.symlink_to(p)
        self.assertEqual(ZCodeRuntime(home,link).artifacts(),())

    def test_zcode_cached_input_detail_is_not_double_counted(self):
        home=self.root/'zcode';p=zcode_fixture(home)
        c=sqlite3.connect(p);c.execute('UPDATE model_usage SET provider_total_tokens=15');c.commit();c.close()
        row=ZCodeRuntime(home).usage()[0]
        self.assertEqual(row.input_tokens,7)
        self.assertEqual(row.input_tokens+row.output_tokens+row.cache_read_tokens,15)

    def test_qwen_message_ids_dedupe_exports_and_exclude_fork_thoughts(self):
        projects=self.root/'projects';exports=self.root/'exports'
        row={'uuid':'u1','sessionId':'s1','timestamp':STAMP,'type':'assistant','cwd':'/workspace/qwen','message':{'parts':[{'text':'answer'},{'text':'secret thinking','thought':True},{'functionCall':{'args':{'secret':'tool-input'}}}]},'usageMetadata':{'promptTokenCount':10,'cachedContentTokenCount':3,'candidatesTokenCount':5,'thoughtsTokenCount':2,'totalTokenCount':17}}
        write_jsonl(projects/'p/chats/s1.jsonl',[row,{**row,'sessionId':'fork','forkedFrom':{'sessionId':'s1'}}])
        write_jsonl(exports/'copy.jsonl',[row])
        r=QwenCodeRuntime(projects,exports)
        self.assertEqual(len(r.dialogue()),1)
        self.assertEqual(r.dialogue()[0].content,'answer')
        self.assertEqual(r.usage()[0].input_tokens,7)
        self.assertEqual(r.usage()[0].protocol_total_tokens,17)

    def test_qwen_malformed_tail_preserves_complete_records(self):
        p=self.root/'p/chats/s.jsonl'
        write_jsonl(p,[{'uuid':'u','sessionId':'s','timestamp':STAMP,'type':'user','message':{'parts':[{'text':'hello'}]}}])
        with p.open('a') as handle:handle.write('{partial')
        r=QwenCodeRuntime(self.root,self.root/'exports')
        self.assertEqual(len(r.dialogue()),1)
        self.assertEqual(r.diagnostics[0]['code'],'invalid-json-line')

    def test_copilot_cli_only_messages_and_allowlisted_plans(self):
        path=self.root/'s/events.jsonl'
        write_jsonl(path,[{'type':'session.start','id':'start','timestamp':STAMP,'data':{'context':{'cwd':'/workspace/copilot'}}},
            {'type':'user.message','id':'u','timestamp':STAMP,'data':{'content':'question'}},
            {'type':'assistant.message','id':'a','timestamp':STAMP,'data':{'content':'answer','encryptedContent':'never-read'}},
            {'type':'assistant.reasoning','id':'r','timestamp':STAMP,'data':{'content':'private reasoning'}},
            {'type':'user.message','id':'hidden','timestamp':STAMP,'data':{'content':'hidden skill','source':'skill-private'}}])
        path.parent.joinpath('plan.md').write_text('# Review evidence')
        path.parent.joinpath('credentials.md').write_text('secret')
        r=CopilotCliRuntime(self.root)
        self.assertEqual(len(r.dialogue()),2)
        self.assertEqual(r.sessions()[0].initial_cwd,'/workspace/copilot')
        self.assertEqual(len(r.documents()),1)
        self.assertEqual(r.usage(),())

    def test_continue_unknown_times_remain_unknown_and_usage_not_invented(self):
        write_json(self.root/'session.json',{'sessionId':'s','title':'Demo','workspaceDirectory':'/workspace/c','usage':{'totalTokens':999},'history':[
            {'message':{'id':'u','role':'user','content':'question'}},
            {'message':{'id':'a','role':'assistant','content':[{'type':'text','text':'answer'},{'type':'thinking','thinking':'private'}]}}]})
        r=ContinueRuntime(self.root)
        self.assertEqual(len(r.dialogue()),2)
        self.assertTrue(all(x.occurred_at is None for x in r.dialogue()))
        self.assertEqual(r.usage(),())

    def test_cline_roles_from_api_not_ui_say_and_usage_once(self):
        write_json(self.root/'t/ui_messages.json',[{'type':'say','say':'text','text':'question','ts':MS},
            {'type':'say','say':'text','text':'answer','ts':MS+1},
            {'type':'say','say':'api_req_started','text':json.dumps({'tokensIn':10,'tokensOut':5,'cacheReads':2,'request':'secret prompt'}),'ts':MS},
            {'type':'say','say':'reasoning','text':'private reasoning','ts':MS}])
        write_json(self.root/'t/api_conversation_history.json',[{'role':'user','content':[{'type':'text','text':'question'}]},
            {'role':'assistant','content':[{'type':'text','text':'answer'},{'type':'thinking','thinking':'private reasoning'}]}])
        r=ClineRuntime([self.root])
        self.assertEqual([m.role for m in r.dialogue()],['user','assistant'])
        self.assertEqual(len(r.usage()),1)
        self.assertTrue(all(m.occurred_at is not None for m in r.dialogue()))

    def test_aider_preserves_export_without_guessing_roles_or_completion(self):
        history=self.root/'project/.aider.chat.history.md';history.parent.mkdir()
        history.write_text('# aider chat started at 2026-09-25 10:00:00\n#### user heading\nmultiline user text\nanswer\n')
        r=AiderRuntime(self.root/'imports',[history])
        self.assertEqual(r.dialogue(),())
        self.assertEqual(len(r.documents()),1)
        self.assertEqual(r.documents()[0].kind,'conversation-export')
        self.assertIsNone(r.sessions()[0].started_at)

    def test_grok_cache_allowlist_dedupe_and_excludes_secret_widgets(self):
        key='sand.client.account.example.transcript.replicas.thread-one'
        name=base64.b32encode(key.encode()).decode().lower().rstrip('=')+'.blob'
        entry={'kind':'message','id':'u','role':'user','content':'question','timestampMs':MS}
        write_json(self.root/name,{'schemaVersion':1,'value':{'entries':[entry,entry,
            {'kind':'send-message','id':'a','timestampMs':MS,'message':{'type':'text','content':'answer'}},
            {'kind':'send-message','id':'secret','timestampMs':MS,'message':{'type':'secret-request','content':'never-import'}},
            {'kind':'message','id':'stream','role':'assistant','isStreaming':True,'content':'partial','timestampMs':MS}]}})
        write_json(self.root/'credentials.json',{'secret':'never-import'})
        r=GrokBotRuntime([self.root])
        self.assertEqual(len(r.artifacts()),1)
        self.assertEqual(len(r.dialogue()),2)
        self.assertEqual(r.usage_status,'unavailable')
        self.assertNotIn('never-import',repr(r.dialogue()))

    def test_antigravity_allowlists_artifacts_and_rejects_symlinks(self):
        brain=self.root/'brain/one';brain.mkdir(parents=True)
        brain.joinpath('walkthrough.md').write_text('# Work verified by source')
        brain.joinpath('upload.md').write_text('private unrelated upload')
        outside=self.root/'outside.md';outside.write_text('outside')
        brain.joinpath('task.md').symlink_to(outside)
        r=AntigravityRuntime({'app':self.root})
        self.assertEqual([d.title for d in r.documents()],['walkthrough.md'])

    def test_factory_missing_sources_never_create_directories(self):
        fields=default_external_tool_settings(self.root/'absent-home')
        for key in NEW_RUNTIME_IDS:
            r=build_runtime(key,fields[key]);self.assertEqual(tuple(r.sessions()),());self.assertEqual(tuple(r.usage()),())
        self.assertFalse((self.root/'absent-home').exists())

    def test_configured_factories_and_skill_targets(self):
        from data_foundation.external_tool_definitions import TOOL_CATALOG, CATALOG_TO_FOUNDATION
        from data_foundation.settings import resolve_external_tool_paths
        paths=initialize_home(self.root/'actanara',legacy_diary_root=self.root/'diary')
        with patch('data_foundation.settings.default_external_tool_settings',return_value=default_external_tool_settings(self.root/'empty')):
            write_settings({'externalTools':{'aider':{'historyFiles':[str(self.root/'project/.aider.chat.history.md')]},'cline':{'taskRootCandidates':[]}}},paths)
            adapters=default_usage_adapters(paths)
            self.assertTrue({CATALOG_TO_FOUNDATION[key] for key in NEW_RUNTIME_IDS}<={a.tool_key for a in adapters})
            self.assertEqual(resolve_external_tool_paths(paths)['cline']['taskRootCandidates'],[])
        for key in ('zcode','cursor','qwenCode','copilotCli'):
            self.assertEqual(TOOL_CATALOG[key]['globalSkillRegistration']['targets'],['skillsRoot'])

    def test_wal_change_invalidates_runtime_fingerprint(self):
        home=self.root/'zcode';p=zcode_fixture(home)
        adapter=LocalRuntimeAdapter(ZCodeRuntime(home),home)
        artifact=tuple(adapter.discover_sources())[0]
        before=adapter.fingerprint(artifact)
        Path(str(p)+'-wal').write_bytes(b'changed-wal-evidence')
        self.assertNotEqual(before,adapter.fingerprint(artifact))

    def test_oversized_and_symlink_file_reads_are_rejected(self):
        target=self.root/'large.json';target.write_text('12345')
        with self.assertRaises(ValueError):read_text(target,self.root,limit=4)
        link=self.root/'link.json';link.symlink_to(target)
        with self.assertRaises(ValueError):read_text(link,self.root)

    def test_new_runtime_ingestion_is_idempotent_and_in_diary_totals(self):
        home=self.root/'zcode';zcode_fixture(home)
        paths=initialize_home(self.root/'actanara',legacy_diary_root=self.root/'diary')
        day=business_date_for(datetime.fromisoformat(STAMP),paths=paths)
        for _ in range(2):
            r=ZCodeRuntime(home)
            result=run_shadow_ingestion(paths,day,adapters=[LocalRuntimeAdapter(r,home)],observe_assets=False)
            self.assertEqual(result.errors,0)
        with connect(paths,read_only=True) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM usage_events WHERE tool_key='zcode'").fetchone()[0],1)
        self.assertEqual(daily_diary_usage_metrics(paths,day)['zcode']['total_tokens'],18)

    def test_missing_new_runtime_usage_does_not_materialize_a_zero(self):
        home=self.root/'zcode';p=zcode_fixture(home)
        c=sqlite3.connect(p);c.execute('DELETE FROM model_usage');c.commit();c.close()
        paths=initialize_home(self.root/'actanara',legacy_diary_root=self.root/'diary')
        day=business_date_for(datetime.fromisoformat(STAMP),paths=paths)
        run_shadow_ingestion(paths,day,adapters=[LocalRuntimeAdapter(ZCodeRuntime(home),home)],observe_assets=False)
        with connect(paths,read_only=True) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM daily_tool_usage WHERE tool_key='zcode'").fetchone()[0],0)
        from app.services.ai_assets import _aggregate_tool
        self.assertEqual(_aggregate_tool('ZCode',[],1)['usageStatus'],'unavailable')

    def test_source_artifacts_reach_narrative_with_evidence_label(self):
        home=self.root/'zcode';zcode_fixture(home)
        at=datetime.fromisoformat(STAMP).timestamp()
        with patch.object(collector,'_local_runtime',return_value=ZCodeRuntime(home)),patch.object(collector,'_diary_root',return_value=self.root/'diary'):
            count=collector.collect_runtime_records('zcode','2026-09-25',at-1,at+1)
        self.assertEqual(count,3)
        text=(self.root/'diary/__diary_daily/2026-09-25/_filtered/zcode/unified_daily.jsonl').read_text()
        self.assertIn('Source-authored artifact',text)

    def test_dashboard_inventory_hides_bodies_and_detail_is_read_only(self):
        home=self.root/'zcode';zcode_fixture(home)
        fields=default_external_tool_settings(self.root/'empty')
        fields['zcode'].update(home=str(home),databasePath=str(home/'cli/db/db.sqlite'))
        with patch.object(service,'resolve_external_tool_paths',return_value=fields):
            data=service.inventory()
            self.assertEqual(len(data['items']),10)
            item=next(i for i in data['items'] if i['id']=='zcode')
            self.assertEqual(item['documentCount'],1)
            self.assertNotIn('question',json.dumps(data))
            detail=service.details('zcode');doc=service.document('zcode',detail['documents'][0]['id'])
            self.assertTrue(doc['readOnly'])
            with self.assertRaises(ValueError):service.details('../../credentials')
            with self.assertRaises(LookupError):service.document('zcode','../../credentials')

    def test_routes_return_error_for_unknown_sources_and_documents(self):
        with patch.object(service,'details',side_effect=ValueError):
            self.assertEqual(asyncio.run(routes.api_runtime_source_details('bad')).status_code,400)
        with patch.object(service,'document',side_effect=LookupError):
            self.assertEqual(asyncio.run(routes.api_runtime_source_document('zcode','missing')).status_code,404)

    def test_successful_routes_use_real_parser_and_no_store(self):
        home=self.root/'zcode';zcode_fixture(home)
        fields=default_external_tool_settings(self.root/'empty')
        fields['zcode'].update(home=str(home),databasePath=str(home/'cli/db/db.sqlite'))
        with patch.object(service,'resolve_external_tool_paths',return_value=fields):
            response=asyncio.run(routes.api_runtime_sources(refresh=True))
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertTrue(json.loads(response.body)['readOnly'])
            detail=json.loads(asyncio.run(routes.api_runtime_source_details('zcode')).body)
            doc=json.loads(asyncio.run(routes.api_runtime_source_document('zcode',detail['documents'][0]['id'])).body)
            self.assertIn('Compare SHA-256',doc['content'])

    def test_narrative_stats_include_new_runtime_without_fabricating_missing_usage(self):
        from diary_generator.narrative_pass import build_matrix_table, _is_blank_diary_activity
        from collections import defaultdict
        metrics=defaultdict(dict,{'zcode':{'total_tokens':15,'messages_count':1},'total':{'total_tokens':15}})
        matrix=build_matrix_table(metrics)
        self.assertIn('zcode',matrix)
        self.assertNotIn('grok-bot',matrix)
        self.assertFalse(_is_blank_diary_activity(metrics,{}))


if __name__ == '__main__':
    unittest.main()
