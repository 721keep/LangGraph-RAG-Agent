from streamlit.testing.v1 import AppTest
for code in ['daily_limit_reached','daily_paused','daily_busy']:
    script="""import sys
sys.path.insert(0,'/workspace/frontend/gui')
import conversation_events as e
import conversation_view as v
turn=e.new_turn()
e.ingest(turn,{'type':'error','code':'%s','content':'UNTRUSTED_PROVIDER_BODY'})
e.finish(turn)
v.delivery(turn)
""" % code
    at=AppTest.from_string(script).run()
    assert not at.exception and len(at.error)==1
    assert 'UNTRUSTED_PROVIDER_BODY' not in str(at.error[0].value)
print('3 Streamlit safe error rendering cases passed')
