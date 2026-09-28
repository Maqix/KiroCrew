"""The home's own Kiro sign-in, one click away (``dashboard/home_signin.py``).

No browser is opened and no AWS or Kiro service is reached: the launch job is a
stored ``LaunchJob`` the watcher reads, and ``cloud.login._open_browser`` is a
recorder. Pins:

* the owner's "Build my home" click records whether their browser is on this
  machine, and only that click's watcher may open a page;
* a sign-in page opens once per code, never for a click from elsewhere, never
  from a watcher resumed after a restart, and never on an unknown host;
* the chat that owns the card gets ONE ``home_signin`` notice, not while it is
  mid-turn.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import launch_job as lj
from kiro_crew.cloud.login_target import KiroLoginTarget
from kiro_crew.dashboard import home_signin, setup_flow
from kiro_crew.dashboard.system_notices import HOME_SIGNIN_KIND, SYSTEM_NOTICE_KINDS

BUILDER_ID_URL = "https://view.awsapps.com/start/#/device?user_code=ABCD-EFGH"


class FakeSlot:
    def __init__(self, key: str = "chat-1-1") -> None:
        self.key = key
        self.messages: list[tuple[str, str, dict | None]] = []
        self.running = False
        self._in_stage_execution = False

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        self.messages.append((role, content, meta))


class FakeState:
    def __init__(self) -> None:
        self.slots = {"chat-1-1": FakeSlot()}
        self.events: list[tuple[str, Any]] = []

    def get_slot(self, key):
        return self.slots.get(key)

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def push_slots_update(self, **_):
        pass


class FakeStore:
    """The launch job store: returns the scripted jobs in turn, the last one after.

    With *issue*, it also plays this process's launch worker: each job whose file
    shows a sign-in is first issued through ``lj._issue_signin``, so the file and
    the worker's in-memory prompt agree. Without it, the file is on its own.
    """

    def __init__(self, jobs: list[lj.LaunchJob], *, issue: bool = True) -> None:
        self.jobs = list(jobs)
        self.issue = issue

    def get(self, job_id):
        job = self.jobs.pop(0) if len(self.jobs) > 1 else self.jobs[0]
        if self.issue and job.signin is not None:
            lj._issue_signin(job, job.signin)
        return job


def _job(status: str, url: str = BUILDER_ID_URL, code: str = "ABCD-EFGH", **kw) -> lj.LaunchJob:
    return lj.LaunchJob(
        id="job-1",
        profile="",
        region="eu-west-1",
        size_key="light",
        status=status,
        signin=lj.SigninPrompt(url=url, code=code) if url else None,
        **kw,
    )


@pytest.fixture
def state():
    return FakeState()


@pytest.fixture(autouse=True)
def _no_issued_signins(monkeypatch):
    """Each test starts in a process whose launch worker has issued nothing."""
    monkeypatch.setattr(lj, "_issued_signins", {})


def _issue(url: str = BUILDER_ID_URL, code: str = "ABCD-EFGH", start_url: str = "") -> None:
    """This process's launch worker issued *url* and *code* for ``job-1``."""
    target = (
        KiroLoginTarget(license="pro", start_url=start_url, region="eu-west-1")
        if start_url
        else KiroLoginTarget()
    )
    lj._issue_signin(_job(lj.AWAITING_SIGNIN, login_target=target), lj.SigninPrompt(url, code))


@pytest.fixture
def opened(monkeypatch):
    """Every page the gateway asks the browser to open."""
    from kiro_crew.cloud import login

    pages: list[str] = []

    def _open(url: str) -> bool:
        pages.append(url)
        return True

    monkeypatch.setattr(login, "_open_browser", _open)
    monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)
    return pages


def _home_card(monkeypatch, *, browser_here: bool | None = None) -> sc.SetupCard:
    from kiro_crew.cloud import iam

    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": True})
    payload, private = setup_flow._home_payload(sc.build_home({"region": "eu-west-1"}))
    if browser_here is not None:
        private[home_signin.BROWSER_HERE_KEY] = browser_here
    card = sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload=payload,
        private=private,
    )

    def _waiting(c: sc.SetupCard) -> None:
        c.status = sc.STATUS_WAITING

    return sc.update_card(card.id, _waiting)


async def _watch(monkeypatch, state, card, jobs, *, may_open: bool, issue: bool = True) -> None:
    from kiro_crew.dashboard import handlers_cloud

    store = FakeStore(jobs, issue=issue)
    monkeypatch.setattr(handlers_cloud, "_store", lambda st: store)
    await asyncio.wait_for(setup_flow._watch_home(state, card.id, "job-1", may_open=may_open), 5)


def _notices(state) -> list[dict]:
    return [meta for _r, _c, meta in state.slots["chat-1-1"].messages if meta]


class TestOpen:
    @pytest.mark.asyncio
    async def test_a_page_opens_once_for_its_code_and_the_chat_hears_once(
        self, state, opened, monkeypatch
    ):
        card = _home_card(monkeypatch, browser_here=True)
        polls = [_job(lj.AWAITING_SIGNIN)] * 3 + [_job(lj.DONE, url="")]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        assert opened == [BUILDER_ID_URL]
        notices = _notices(state)
        assert notices == [{"kind": HOME_SIGNIN_KIND, "opened": True, "card": card.id}]
        assert "opened in your browser" in state.slots["chat-1-1"].messages[0][1]
        assert sc.get_card(card.id).outcome["ready"] is True

    @pytest.mark.asyncio
    async def test_a_new_code_opens_its_own_page_but_the_notice_stays_one(
        self, state, opened, monkeypatch
    ):
        card = _home_card(monkeypatch, browser_here=True)
        fresh = "https://view.awsapps.com/start/#/device?user_code=WXYZ-1234"
        polls = [
            _job(lj.AWAITING_SIGNIN),
            _job(lj.AWAITING_SIGNIN, url=fresh, code="WXYZ-1234"),
            _job(lj.AWAITING_SIGNIN, url=fresh, code="WXYZ-1234"),
            _job(lj.DONE, url=""),
        ]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        assert opened == [BUILDER_ID_URL, fresh]
        assert len(_notices(state)) == 1

    @pytest.mark.asyncio
    async def test_a_click_from_another_machine_opens_nothing(self, state, opened, monkeypatch):
        card = _home_card(monkeypatch, browser_here=False)
        await _watch(
            monkeypatch,
            state,
            card,
            [_job(lj.AWAITING_SIGNIN), _job(lj.DONE, url="")],
            may_open=False,
        )
        assert opened == []
        assert _notices(state) == [{"kind": HOME_SIGNIN_KIND, "opened": False, "card": card.id}]
        assert "Open the sign-in link on its card" in state.slots["chat-1-1"].messages[0][1]

    @pytest.mark.asyncio
    async def test_a_watcher_resumed_after_a_restart_opens_nothing(
        self, state, opened, monkeypatch
    ):
        # The card says the click came from here, but this watcher is not that
        # click's: the owner may have left the machine since.
        card = _home_card(monkeypatch, browser_here=True)
        await _watch(
            monkeypatch,
            state,
            card,
            [_job(lj.AWAITING_SIGNIN), _job(lj.DONE, url="")],
            may_open=False,
        )
        assert opened == []

    @pytest.mark.asyncio
    async def test_a_page_already_opened_for_this_code_is_not_opened_again(
        self, state, opened, monkeypatch
    ):
        card = _home_card(monkeypatch, browser_here=True)
        polls = [_job(lj.AWAITING_SIGNIN), _job(lj.DONE, url="")]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        # A second watcher on the same card and the same code: nothing more.

        def _rewait(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_WAITING

        sc.update_card(card.id, _rewait)
        await _watch(monkeypatch, state, card, polls, may_open=True)
        assert opened == [BUILDER_ID_URL]
        assert len(_notices(state)) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_host_is_never_opened(self, state, opened, monkeypatch):
        card = _home_card(monkeypatch, browser_here=True)
        polls = [
            _job(lj.AWAITING_SIGNIN, url="https://example-bucket.s3.amazonaws.com/?user_code=X"),
            _job(lj.DONE, url=""),
        ]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        assert opened == []
        assert _notices(state)[0]["opened"] is False

    @pytest.mark.asyncio
    async def test_an_identity_center_portal_on_its_own_domain_is_opened(
        self, state, opened, monkeypatch
    ):
        card = _home_card(monkeypatch, browser_here=True)
        target = KiroLoginTarget(
            license="pro", start_url="https://sso.example.org/start", region="eu-west-1"
        )
        url = "https://sso.example.org/start/#/device?user_code=ABCD-EFGH"
        polls = [
            _job(lj.AWAITING_SIGNIN, url=url, login_target=target),
            _job(lj.DONE, url="", login_target=target),
        ]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        assert opened == [url]

    @pytest.mark.asyncio
    async def test_a_sign_in_rewritten_in_the_job_file_is_never_opened(
        self, state, opened, monkeypatch
    ):
        # The job file is writable from the agent's sandbox. A code swapped in
        # there is the agent's own device code: approving it would sign its
        # session in. Neither the card nor the browser ever gets it: both take
        # the page the worker issued, which is what opens.
        card = _home_card(monkeypatch, browser_here=True)
        _issue()
        planted = "https://view.awsapps.com/start/#/device?user_code=EVIL-0000"
        polls = [
            _job(lj.AWAITING_SIGNIN, url=planted, code="EVIL-0000"),
            _job(lj.DONE, url=""),
        ]
        await _watch(monkeypatch, state, card, polls, may_open=True, issue=False)
        assert planted not in opened
        assert opened == [BUILDER_ID_URL]

    @pytest.mark.asyncio
    async def test_a_start_url_rewritten_in_the_job_file_does_not_widen_the_hosts(
        self, state, opened, monkeypatch
    ):
        # The worker issued a page on a host no Kiro sign-in lives on, with no
        # Identity Center target; the file then names that host as its start URL.
        card = _home_card(monkeypatch, browser_here=True)
        url = "https://sso.example.org/start/#/device?user_code=ABCD-EFGH"
        _issue(url=url)
        forged = KiroLoginTarget(
            license="pro", start_url="https://sso.example.org/start", region="eu-west-1"
        )
        polls = [
            _job(lj.AWAITING_SIGNIN, url=url, login_target=forged),
            _job(lj.DONE, url="", login_target=forged),
        ]
        await _watch(monkeypatch, state, card, polls, may_open=True, issue=False)
        assert opened == []

    @pytest.mark.asyncio
    async def test_a_sign_in_no_worker_here_issued_is_never_opened(
        self, state, opened, monkeypatch
    ):
        # After a restart, or from another process: the file is all there is.
        card = _home_card(monkeypatch, browser_here=True)
        polls = [_job(lj.AWAITING_SIGNIN), _job(lj.DONE, url="")]
        await _watch(monkeypatch, state, card, polls, may_open=True, issue=False)
        assert opened == []
        assert _notices(state)[0]["opened"] is False

    @pytest.mark.asyncio
    async def test_a_browser_that_does_not_open_says_so(self, state, monkeypatch):
        from kiro_crew.cloud import login

        tried: list[str] = []

        def _refuse(url: str) -> bool:
            tried.append(url)
            return False

        monkeypatch.setattr(login, "_open_browser", _refuse)
        monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)
        card = _home_card(monkeypatch, browser_here=True)
        polls = [_job(lj.AWAITING_SIGNIN)] * 2 + [_job(lj.DONE, url="")]
        await _watch(monkeypatch, state, card, polls, may_open=True)
        # Tried once for the code; the notice sends the owner to the card.
        assert tried == [BUILDER_ID_URL]
        assert _notices(state) == [{"kind": HOME_SIGNIN_KIND, "opened": False, "card": card.id}]


class TestNotice:
    @pytest.mark.asyncio
    async def test_the_notice_waits_while_the_chat_is_mid_turn(self, state, opened, monkeypatch):
        card = _home_card(monkeypatch, browser_here=True)
        slot = state.slots["chat-1-1"]
        slot.running = True
        _issue()
        await home_signin.prompt_signin(
            state, card, {"url": BUILDER_ID_URL, "code": "ABCD-EFGH"}, job_id="job-1", may_open=True
        )
        # The page opens at once; the notice does not land inside the reply.
        assert opened == [BUILDER_ID_URL] and slot.messages == []
        slot.running = False
        await home_signin.prompt_signin(
            state,
            sc.get_card(card.id),
            {"url": BUILDER_ID_URL, "code": "ABCD-EFGH"},
            job_id="job-1",
            may_open=True,
        )
        assert opened == [BUILDER_ID_URL]
        assert _notices(state) == [{"kind": HOME_SIGNIN_KIND, "opened": True, "card": card.id}]

    def test_the_notice_is_a_system_notice(self):
        assert HOME_SIGNIN_KIND in SYSTEM_NOTICE_KINDS

    @pytest.mark.asyncio
    async def test_a_simulated_home_opens_nothing(self, state, opened, monkeypatch):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        card = _home_card(monkeypatch, browser_here=True)
        _issue(code="X")
        await home_signin.prompt_signin(
            state, card, {"url": BUILDER_ID_URL, "code": "X"}, job_id="job-1", may_open=True
        )
        assert opened == []


class TestTheClick:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("here", [True, False])
    async def test_build_records_the_browser_and_hands_the_watcher_its_answer(
        self, state, monkeypatch, here
    ):
        from kiro_crew.dashboard import handlers_cloud, setup_aws_signin

        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        monkeypatch.setattr(setup_aws_signin, "browser_is_here", lambda same_machine: here)

        async def _no_target():
            return None

        async def _start(state_, **kw):
            return _job(lj.RUNNING, url=""), None

        watched: dict[str, Any] = {}

        async def _watch_home(state_, card_id, job_id, *, may_open=False):
            watched["may_open"] = may_open

        monkeypatch.setattr(setup_flow, "_inherited_login_target", _no_target)
        monkeypatch.setattr(handlers_cloud, "start_launch_job", _start)
        monkeypatch.setattr(setup_flow, "_watch_home", _watch_home)
        card = _home_card(monkeypatch)

        def _pending(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_PENDING

        sc.update_card(card.id, _pending)
        building = await setup_flow.decide(
            state, card.id, "commit", card.payload_hash, {}, same_machine=here
        )
        await asyncio.sleep(0)
        assert building.status == sc.STATUS_WAITING
        assert sc.get_card(card.id).private[home_signin.BROWSER_HERE_KEY] is here
        assert watched == {"may_open": here}


class TestOpenable:
    @pytest.mark.parametrize(
        "url",
        [
            BUILDER_ID_URL,
            "https://d-1234567890.awsapps.com/start/#/device?user_code=ABCD-EFGH",
            "https://device.sso.us-east-1.amazonaws.com/?user_code=ABCD-EFGH",
            "https://app.kiro.dev/device?user_code=ABCD-EFGH",
        ],
    )
    def test_kiro_sign_in_hosts_open(self, url):
        assert home_signin.openable(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://view.awsapps.com/start/#/device?user_code=X",
            "https://example-bucket.s3.amazonaws.com/login",
            "https://user:pw@view.awsapps.com/start",
            "https://view.awsapps.com:8443/start",
            "https://awsapps.com.evil.example/start",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_anything_else_stays_a_link(self, url):
        assert not home_signin.openable(url)

    def test_the_login_targets_own_portal_opens(self):
        start = "https://sso.example.org/start"
        assert home_signin.openable("https://sso.example.org/start/#/device", start)
        assert not home_signin.openable("https://other.example.org/start/#/device", start)


class TestTheCardShowsWhatWasIssued:
    """The card's link and code come from the worker's own copy, not the job file."""

    def test_a_planted_code_in_the_job_file_is_not_shown(self):
        from kiro_crew.dashboard.setup_flow import _home_outcome

        _issue(BUILDER_ID_URL, "ABCD-EFGH")
        planted = _job(
            lj.AWAITING_SIGNIN,
            url="https://view.awsapps.com/start/#/device?user_code=EVIL-0000",
            code="EVIL-0000",
        )
        shown = _home_outcome(planted)["signin"]
        assert shown == {"url": BUILDER_ID_URL, "code": "ABCD-EFGH"}

    def test_after_a_restart_the_file_copy_is_shown_as_before(self):
        from kiro_crew.dashboard.setup_flow import _home_outcome

        shown = _home_outcome(_job(lj.AWAITING_SIGNIN))["signin"]
        assert shown == {"url": BUILDER_ID_URL, "code": "ABCD-EFGH"}

    def test_a_finished_sign_in_shows_no_link_even_with_an_old_record(self):
        from kiro_crew.dashboard.setup_flow import _home_outcome

        _issue(BUILDER_ID_URL, "ABCD-EFGH")
        assert "signin" not in _home_outcome(_job(lj.RUNNING, url=""))
