import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import pytest
from PIL import Image

from drivers.web_driver import (
    DriverError, KIND_ABNORMAL, KIND_TIMEOUT, KIND_VERIFY, WebDriver, fingerprint_from_src,
)
from drivers.web_profiles import (
    ProfileError, build_profile, find_profile_for_url, load_profiles,
)

WYLKYJ = load_profiles()["wylkyj"]

playwright_sync = pytest.importorskip("playwright.sync_api")

PAPERS = ["1001A.png", "1002A.png", "1003A.png", "1004A.png"]

PAGE_HTML = """
<div class="imgSection"></div>
<input id="inputOne" type="text"
  oninput="value=value.replace(/[^\\d.]/g,'').replace(/(\\.{1}[0-9]{2})([0-9]+)$/,'$1').slice(0,5)">
<button class="btnSubmit">submit</button>
<script>
 const papers = %s; let idx = 0;
 const box = document.querySelector('.imgSection');
 function render() {
   box.innerHTML = '';
   papers.slice(idx, idx + 3).forEach((p, k) => {
     const i = document.createElement('img');
     i.src = '/img/' + p + '?Expires=1&security-token=SECRET';
     if (k > 0) i.style.display = 'none';
     box.appendChild(i);
   });
   document.getElementById('inputOne').value = '';
 }
 document.querySelector('.btnSubmit').onclick = () => { idx++; render(); };
 render();
</script>
""" % PAPERS


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/img/"):
            buf = BytesIO()
            Image.new("RGB", (60, 30), (200, 200, 200)).save(buf, format="PNG")
            body, ctype = buf.getvalue(), "image/png"
        else:
            body, ctype = PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def page():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            pytest.skip("本机没有可用的 Edge")
        pg = browser.new_page()
        pg.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        yield pg
        browser.close()
    server.shutdown()


@pytest.fixture
def driver(page):
    page.reload()
    return WebDriver(WYLKYJ, page=page)


def test_fingerprint_drops_token_query():
    assert fingerprint_from_src("https://x.com/a/b/1521A.png?Expires=1&security-token=SECRET") == "1521A.png"


def test_profile_matches_by_url():
    assert find_profile_for_url("https://www.wylkyj.com/yuejuan/#/reading") == WYLKYJ
    assert find_profile_for_url("https://example.com") is None


def test_builtin_wylkyj_profile_unchanged():
    assert WYLKYJ.image_selectors == (".imgSection img",)
    assert WYLKYJ.input_selectors == ("#inputOne",)
    assert WYLKYJ.submit_selectors == ("button.btnSubmit",)
    assert WYLKYJ.abnormal_selectors == ('button:has-text("标记异常试卷")',)


def _write(dirpath, name, text):
    (dirpath / name).write_text(text, encoding="utf-8")


def test_profile_extends_overrides_only_given_fields(tmp_path):
    _write(tmp_path, "base.json",
           '{"name":"A","url_contains":"a.com","image":".img","input":"#in","submit":"#ok"}')
    _write(tmp_path, "child.json",
           '{"extends":"base","name":"B","url_contains":["b.com"],"input":["#x","#y"]}')
    profiles = load_profiles([str(tmp_path)])
    child = profiles["child"]
    assert child.name == "B" and child.url_contains == ("b.com",)
    assert child.input_selectors == ("#x", "#y")
    assert child.image_selectors == (".img",) and child.submit_selectors == ("#ok",)


def test_bad_profile_is_skipped_without_breaking_others(tmp_path):
    _write(tmp_path, "good.json", '{"url_contains":"g.com","image":".i","input":"#n"}')
    _write(tmp_path, "broken.json", "{not json")
    _write(tmp_path, "cycle.json", '{"extends":"cycle","url_contains":"c.com","image":".i","input":"#n"}')
    _write(tmp_path, "incomplete.json", '{"url_contains":"x.com"}')
    assert set(load_profiles([str(tmp_path)])) == {"good"}


def test_later_directory_overrides_same_name(tmp_path):
    d1, d2 = tmp_path / "one", tmp_path / "two"
    d1.mkdir(); d2.mkdir()
    _write(d1, "p.json", '{"name":"old","url_contains":"p.com","image":".i","input":"#n"}')
    _write(d2, "p.json", '{"name":"new","url_contains":"p.com","image":".i","input":"#n"}')
    assert load_profiles([str(d1), str(d2)])["p"].name == "new"


def test_candidate_selectors_fall_back_in_order(page):
    page.reload()
    prof = build_profile({"url_contains": "x", "image": ["#nope img", ".imgSection img"],
                          "input": ["#missing", "#inputOne"], "submit": [".nothing", "button.btnSubmit"]})
    drv = WebDriver(prof, page=page)
    assert drv.capture().startswith("data:image/jpeg")
    drv.enter_score("2")
    drv.confirm()
    assert drv.wait_next_paper(timeout=5) == "1002A.png"


def test_capture_picks_visible_image_and_returns_jpeg(driver):
    data_uri = driver.capture()
    assert data_uri.startswith("data:image/jpeg;base64,")
    assert driver.captured_fingerprint == "1001A.png"
    assert driver.is_still_same_paper()


def test_enter_score_readback_and_filter_mismatch(driver):
    driver.capture()
    driver.enter_score("5.5")
    with pytest.raises(DriverError) as e:
        driver.enter_score("5.555")  # 页面的 oninput 会截成 5.55，必须被读回校验拦下
    assert e.value.kind == KIND_VERIFY


def test_confirm_then_wait_next_paper(driver):
    driver.capture()
    driver.enter_score("3")
    driver.confirm()
    assert driver.wait_next_paper(timeout=5) == "1002A.png"
    assert not driver.is_still_same_paper()


def test_wait_next_paper_times_out_when_page_does_not_change(driver):
    driver.capture()
    with pytest.raises(DriverError) as e:
        driver.wait_next_paper(timeout=0.8)
    assert e.value.kind == KIND_TIMEOUT


def test_abnormal_paper_detected_and_marked(driver, page):
    # 实测的五岳异常卷：没有 .imgSection、分数框被禁用
    page.evaluate("""() => {
        document.querySelector('.imgSection').remove();
        document.getElementById('inputOne').disabled = true;
        const b = document.createElement('button');
        b.textContent = '标记异常试卷';
        b.onclick = () => { window.marked = true; };
        document.body.appendChild(b);
    }""")
    with pytest.raises(DriverError) as e:
        driver.capture(timeout=6)
    assert e.value.kind == KIND_ABNORMAL
    driver.mark_abnormal()
    assert page.evaluate("window.marked") is True


def _web_thread(driver):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from unittest.mock import MagicMock
    from auto_thread import GradingThread
    from config_manager import ConfigManager
    from ui_schema import load_ui_schema

    cm = ConfigManager.__new__(ConfigManager)
    cm.max_questions = load_ui_schema().max_questions
    cm._init_default_config()
    thread = GradingThread(MagicMock(), cm)
    thread.running = True
    thread.completion_status = "running"
    thread._web_driver = driver
    thread.select_and_build_prompt = lambda *a, **k: {"system": "S", "user": "U"}
    thread.evaluate_answer = lambda *a, **k: (3.0, {}, [], {}, "raw")
    thread._check_hallucination_guard = lambda *a, **k: None
    thread.record_grading_result = MagicMock()
    return thread, cm


def test_thread_grades_one_paper_through_web_driver(driver):
    thread, cm = _web_thread(driver)
    q_config = dict(cm.question_configs["1"], question_index=1)  # 无任何屏幕坐标
    assert thread._process_single_question(q_config, 0, 1) is True
    assert driver.current_fingerprint() == "1002A.png"  # 已提交并换到下一份
    assert thread.record_grading_result.call_count == 1


def test_thread_stops_instead_of_writing_when_paper_changed_during_grading(driver, page):
    thread, cm = _web_thread(driver)

    def slow_eval(*a, **k):
        page.click(".btnSubmit")  # 模拟 AI 评分期间平台自行换卷
        return (3.0, {}, [], {}, "raw")

    thread.evaluate_answer = slow_eval
    q_config = dict(cm.question_configs["1"], question_index=1)
    assert thread._process_single_question(q_config, 0, 1) is False
    assert not thread.running
    assert thread.record_grading_result.call_count == 0
    assert page.locator("#inputOne").input_value() == ""  # 没有把分数写到新卷上
