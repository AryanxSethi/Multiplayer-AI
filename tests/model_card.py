"""Drive the live model card and workspace join box from tests.

AgentDialog and JoinDialog are gone. Nothing in the app can reach them, so the
tests that drove them were driving a second, stale implementation of the same
thing -- and they kept it alive. These helpers go through the widgets a reader
actually touches.

They live here rather than on one test case because three test modules need
them, and because each one encodes a trap that is expensive to rediscover.
"""

from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.window import AGENTS_PAGE, WORKSPACES_PAGE


def model_card(window):
    """Open the model card, which is where a model is connected or edited now."""
    window.navigate(AGENTS_PAGE)
    return window


def _agent_ready(window, agent_id):
    entry = window.network.runtime.engine.runners.get(agent_id)
    return bool(entry and entry[0] is not None and entry[0].ready.is_set())


def wait_for_agent(case, window, agent_id):
    """Wait until the agent is listed online and, when it is ours, running.

    The online flag is not enough. save_agent has returned by the time the relay
    announces the agent, but its profile has not necessarily started, so a send
    issued in between reaches an agent with nothing behind it. A remote window
    does not run the agent, so only its host can answer that, and there the
    listing is all there is to wait for.

    A joining guest learns the agent list from its first poll after the join,
    which is why the remote wait is the longer one.
    """
    case.wait(lambda: any(a["id"] == agent_id and a["online"] for a in window.agents),
              30 if window.remote else 15)
    if not window.remote:
        case.wait(lambda: _agent_ready(window, agent_id))
    return window


def model_error_text(window):
    """The card's validation line, which replaces the dialog error box."""
    line = window.model_error_line
    return line.message.text() if line.isVisible() else ""


def save_model_card(case, window, expect_error=False):
    """Press Save and wait for the card to finish with the save.

    AgentDialog closed on success and stayed open on failure, so its closing was
    an honest signal. The card never closes, and its save button is enabled
    before and after a save, because save_model re-enables it on failure too and
    never relabels it. So the only honest signal is the window's in-flight flag,
    and it is an honest one: save_agent is fully awaited, so the vault write
    happens inside it and the flag clears only once everything has been
    persisted.

    Waiting for the saved profile instead would be wrong: saving an unchanged
    profile leaves it identical, and the restart test does exactly that.
    """
    window.model_save_button.click()
    if expect_error:
        case.wait(lambda: bool(model_error_text(window)))
        return window
    case.wait(lambda: window.busy)
    case.wait(lambda: not window.busy)
    return window


def edit_model(case, profile, window=None):
    """Load a profile into the model card the way the app itself does.

    edit_agent takes an entry from the agent list, not a bare profile. It reads
    agent["profile"], and a bare profile has no such key, so it is handed {} and
    loads an empty form instead: no model id, search off, no keys. The save then
    fails validation on the empty model id and never persists, so the test goes
    on to read back the previous, stale profile. That is where a web_search of
    "off" and an empty vault both came from, and they looked like two unrelated
    bugs until the empty form explained both at once.
    """
    window = model_card(window or profile["window"])
    window.edit_agent({"id": profile["id"], "provider": profile["provider"],
                       "profile": profile})
    case.wait(lambda: window.model_id.currentText() == profile["model"])
    return window


def join_workspace(case, window, invitation):
    """Join through the workspace card's invitation box.

    JoinDialog reported a bad invitation by staying open. The card writes the
    refusal on its join line, so wait for the room to be selected rather than for
    a dialog to close.
    """
    window.navigate(WORKSPACES_PAGE)
    window.workspace_invitation.setPlainText(invitation)
    window.workspace_join_button.click()
    return window


def connect_model(case, window, name, handler=None, model="test-model",
                  vision=False, provider="bionic", agent_id=None):
    """Connect a model through the card, pointing it at a mock provider.

    Provider defaults to bionic rather than ollama on purpose. The card checks
    ollama for installed models before it will save, and a machine without
    ollama has none, so an ollama card fails to save at all. A mock tags route
    is registered for the ollama case, but the bionic route is the one these
    tests actually use.
    """
    if handler is not None:
        window.network.runtime.app.router.routes.append(
            Route("/mock/v1/chat/completions", handler, methods=["POST"]))
    async def installed(request):
        return JSONResponse({"models": [{"name": "test-model:latest"}]})
    window.network.runtime.app.router.routes.append(
        Route("/mock/v1/api/tags", installed, methods=["GET"]))
    window = model_card(window)
    window.model_provider.setCurrentIndex(window.model_provider.findData(provider))
    window.change_model_provider()
    window.model_name.setText(name)
    window.model_base.setText(f"http://127.0.0.1:{window.port}/mock/v1")
    window.model_id.setCurrentText(model)
    window.model_key.setText("test-key")
    window.model_vision.setChecked(vision)
    save_model_card(case, window)
    wait_for_agent(case, window, agent_id or name)
    window.select_agent(name)
    return window
