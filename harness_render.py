"""Headless harness: stub streamlit/plotly/detection so we can import dashboard/app.py
and actually run the rewritten pages against the real db_manager — catching runtime errors."""
import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- stub streamlit ----
st = types.ModuleType("streamlit")

class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False

class _SS(dict):
    def __getattr__(self, k): return self.get(k)
    def __setattr__(self, k, v): self[k] = v
    def setdefault(self, k, v=None): return super().setdefault(k, v)

st.session_state = _SS()

def _noop(*a, **k): return None
def _ret(v):
    def f(*a, **k): return v
    return f

class _Col(_Ctx):
    def button(self, *a, **k): return False
    def selectbox(self, label, options, *a, **k): return options[0] if options else None
    def caption(self, *a, **k): return None
    def markdown(self, *a, **k): return None
    def metric(self, *a, **k): return None
    def number_input(self, *a, **k): return k.get("value", 0)
    def text_input(self, *a, **k): return ""
    def download_button(self, *a, **k): return False
    def file_uploader(self, *a, **k): return None
    def image(self, *a, **k): return None
    def success(self, *a, **k): return None
    def info(self, *a, **k): return None
    def warning(self, *a, **k): return None
    def error(self, *a, **k): return None
    def columns(self, spec, *a, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

def columns(spec, *a, **k):
    n = spec if isinstance(spec, int) else len(spec)
    return [_Col() for _ in range(n)]

def tabs(labels): return [_Ctx() for _ in labels]

st.set_page_config = _noop
st.markdown = _noop
st.caption = _noop
st.info = _noop
st.warning = _noop
st.error = _noop
st.success = _noop
st.dataframe = _noop
st.code = _noop
st.metric = _noop
st.plotly_chart = _noop
st.radio = lambda label, options=None, *a, **k: (options[0] if options else None)
st.toggle = lambda *a, **k: k.get("value", True)
st.button = _ret(False)
st.download_button = _ret(False)
st.link_button = _noop
st.selectbox = lambda label, options, *a, **k: (options[0] if options else None)
st.text_input = _ret("")
st.text_area = _ret("")
st.number_input = lambda *a, **k: k.get("value", 0)
st.date_input = lambda *a, **k: __import__("datetime").date.today()
st.form_submit_button = _ret(False)
st.rerun = _noop
st.stop = _noop
st.columns = columns
st.tabs = tabs
st.form = lambda *a, **k: _Ctx()
st.expander = lambda *a, **k: _Ctx()
st.container = lambda *a, **k: _Ctx()
st.spinner = lambda *a, **k: _Ctx()
st.sidebar = _Ctx()
st.sidebar.markdown = _noop
st.sidebar.radio = _ret(None)
st.sidebar.button = _ret(False)
st.sidebar.toggle = _ret(True)
st.image = _noop
st.file_uploader = _ret(None)
st.divider = _noop
st.toast = _noop
sys.modules["streamlit"] = st

# ---- stub plotly ----
px = types.ModuleType("plotly.express")
for fn in ["line", "bar", "pie", "scatter", "area", "histogram"]:
    setattr(px, fn, _ret(_Fig() if False else None))
go = types.ModuleType("plotly.graph_objects")
class _Fig:
    def add_trace(self, *a, **k): return self
    def update_layout(self, *a, **k): return self
    def update_xaxes(self, *a, **k): return self
    def update_yaxes(self, *a, **k): return self
    def add_hline(self, *a, **k): return self
    def update_traces(self, *a, **k): return self
    def add_annotation(self, *a, **k): return self
    def add_vline(self, *a, **k): return self
go.Figure = _Fig
go.Scatter = _ret(None)
go.Bar = _ret(None)
go.Pie = _ret(None)
plotly = types.ModuleType("plotly")
plotly.express = px; plotly.graph_objects = go
sys.modules["plotly"] = plotly
sys.modules["plotly.express"] = px
sys.modules["plotly.graph_objects"] = go

# fix px functions to return a _Fig
for fn in ["line", "bar", "pie", "scatter", "area", "histogram"]:
    setattr(px, fn, _ret(_Fig()))

# ---- stub streamlit_autorefresh ----
sar = types.ModuleType("streamlit_autorefresh")
sar.st_autorefresh = _noop
sys.modules["streamlit_autorefresh"] = sar

# ---- import REAL detection.advanced_pipeline so a broken class is caught here.
#      Only fall back to a stub if cv2/YOLO genuinely can't load in this sandbox. ----
try:
    import detection.advanced_pipeline as _real_ap
    assert hasattr(_real_ap, "AdvancedOverspeedPipeline"), "AdvancedOverspeedPipeline missing!"
    print("real detection.advanced_pipeline imported OK (AdvancedOverspeedPipeline present)")
except Exception as _e:
    print(f"  (real pipeline import failed, using stub: {repr(_e)[:80]})")
    ap = types.ModuleType("detection.advanced_pipeline")
    class AdvancedOverspeedPipeline:  # noqa
        def __init__(self, *a, **k): pass
    ap.AdvancedOverspeedPipeline = AdvancedOverspeedPipeline
    ap._demo_plate_for = lambda t: "MH01AB1234"
    sys.modules["detection.advanced_pipeline"] = ap

# ---- import the app ----
import importlib.util
spec = importlib.util.spec_from_file_location("oapp", os.path.join("dashboard", "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
print("app.py imported OK (module-level code ran: set_page_config, CSS, init_database)")

# ---- seed data so pages have content ----
from database import db_manager as db
admin = db.authenticate_user("admin@speedcam.com", "admin123")
if db.authenticate_owner("harness@ex.com", "Pass123!") is None:
    db.create_owner_account("Harness Owner", "harness@ex.com", "Pass123!", "9000000000")
owner = db.authenticate_owner("harness@ex.com", "Pass123!")
db.add_registered_vehicle(owner["owner_id"], "MH12HARNES", "car", "Sedan")
cam = db.get_all_cameras()[0]["camera_id"]
sess = db.create_session(cam, "harness.mp4", "file", "harness")
vid = db.upsert_vehicle(sess, "H1", "car", "MH12HARNES")
for spd in [80.0, 100.0, 120.0]:
    sp = db.insert_speed_record(vid, spd, 60.0); db.insert_violation(sp, spd, 60.0)
db.bulk_issue_challans_for_all(admin["user_id"], "both")

# ---- exercise pages ----
app.bootstrap_state()
st.session_state.authority = admin
st.session_state.owner = None
st.session_state.live_refresh = False
for name in ["page_command_center", "page_notice_desk", "page_reports",
             "page_users", "page_security", "page_system_health"]:
    getattr(app, name)()
    print(f"  authority {name}() ran OK")

st.session_state.authority = None
st.session_state.owner = owner
for name in ["page_owner_notices", "page_owner_vehicles", "page_owner_security"]:
    getattr(app, name)()
    print(f"  owner {name}() ran OK")

# exercise the UPI checkout panel by forcing one challan into checkout mode
_notices = db.get_owner_notices(owner["owner_id"])
if _notices:
    _nid = _notices[0]["notice_id"]
    st.session_state[f"checkout_{_nid}"] = True
    app.page_owner_notices()
    print("  owner checkout panel (render_checkout) ran OK")

# login screen + portals
st.session_state.owner = None
st.session_state.portal = None
app.render_login_tabs(); print("  render_landing() ran OK")
st.session_state.portal = "authority"
app.render_login_tabs(); print("  render_authority_portal() ran OK")
st.session_state.portal = "owner"
app.render_login_tabs(); print("  render_owner_portal() ran OK")
app.top_navbar("home"); print("  top_navbar() ran OK")
st.session_state.authority = admin; st.session_state.owner = None
app.top_nav("Traffic Authority", "Demo", ["Command Center", "Live Monitor"], "authority_page")
print("  top_nav() ran OK")

print("\nALL PAGES RENDERED WITHOUT RUNTIME ERRORS ✅")