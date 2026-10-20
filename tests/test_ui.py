from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import services
from tests.helpers import make_campaign


def _app():
    return AppTest.from_file(str(Path(__file__).resolve().parent.parent / "ui" / "streamlit_app.py"), default_timeout=60)


def test_all_pages_render():
    at = _app().run()
    assert not at.exception
    for page in ["New campaign", "Review", "History", "Settings", "Dashboard"]:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page


def test_review_and_approve_in_ui():
    cid, _ = make_campaign(platforms=("linkedin",))
    at = _app()
    at.session_state["campaign_id"] = cid
    at.session_state["page"] = "Review"
    at.run()
    assert not at.exception
    approve = [b for b in at.button if b.label == "Approve this revision"]
    assert approve
    approve[0].click().run()
    assert not at.exception
    assert services.get_campaign(cid)["posts"][0]["status"] == "APPROVED"
    prepare = [b for b in at.button if b.label == "Prepare to publish"]
    prepare[0].click().run()
    assert not at.exception
    assert services.get_campaign(cid)["posts"][0]["status"] == "READY_TO_PUBLISH"


def test_new_campaign_form_creates_job():
    at = _app()
    at.session_state["page"] = "New campaign"
    at.run()
    at.text_input[0].set_value("Understanding Docker volumes")
    [b for b in at.button if b.label == "Create and generate"][0].click().run()
    assert not at.exception
    assert services.list_campaigns()
