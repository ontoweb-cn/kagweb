"""Interactive runtime settings initializer.

``kagweb init`` walks the user through a four-step wizard (ports → LLM →
search → review) that writes the same files as the Web Settings page.

Heavy lifting (provider menu, live ``/models`` fetch, connectivity probe,
review panel) lives in :mod:`kagweb_cli.init_wizard`. This module is
intentionally thin so the order of steps is easy to read top-to-bottom.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
import typer

from kagweb.runtime.home import KAGWEB_HOME_ENV, get_runtime_home

from . import init_wizard as wiz


def _reset_runtime_singletons() -> None:
    """Drop cached service instances so the new KAGWEB_HOME takes effect.

    ``kagweb init`` may pass ``--home`` to target a different workspace; the
    singletons cache paths from the *previous* PathService and will silently
    write to the wrong place if not cleared.
    """
    try:
        from kagweb.services.path_service import PathService

        PathService.reset_instance()
    except Exception:
        pass
    try:
        from kagweb.services.config.runtime_settings import RuntimeSettingsService

        RuntimeSettingsService._instances.clear()
    except Exception:
        pass
    try:
        from kagweb.services.config.model_catalog import ModelCatalogService

        ModelCatalogService._instances.clear()
    except Exception:
        pass


def _ensure_model_service(catalog: dict, service_name: str, profile_id: str, model_id: str):
    """Locate or create the default profile + model rows we'll mutate in place."""
    services = catalog.setdefault("services", {})
    service = services.setdefault(
        service_name,
        {"active_profile_id": profile_id, "active_model_id": model_id, "profiles": []},
    )
    profiles = service.setdefault("profiles", [])
    profile = next(
        (item for item in profiles if item.get("id") == service.get("active_profile_id")), None
    )
    if profile is None:
        profile = {
            "id": profile_id,
            "name": "Default LLM Endpoint"
            if service_name == "llm"
            else "Default Embedding Endpoint",
            "binding": "openai",
            "base_url": "",
            "api_key": "",
            "api_version": "",
            "extra_headers": {},
            "models": [],
        }
        profiles.append(profile)
        service["active_profile_id"] = profile_id
    models = profile.setdefault("models", [])
    model = next(
        (item for item in models if item.get("id") == service.get("active_model_id")), None
    )
    if model is None:
        model = {"id": model_id, "name": "Default Model", "model": ""}
        models.append(model)
        service["active_model_id"] = model_id
    return profile, model


def _llm_step(
    console: Console,
    strings: dict,
    current_profile: dict,
    current_model: dict,
) -> wiz.LLMChoice:
    spec = wiz.select_llm_provider(
        console,
        strings,
        current_binding=str(current_profile.get("binding") or "openai"),
    )

    if spec is not None:
        binding = spec.name
        default_base = spec.default_api_base or str(current_profile.get("base_url") or "")
        display_provider = spec.label
        env_key = spec.env_key
    else:
        binding = (
            typer.prompt(
                strings["init.binding"],
                default=str(current_profile.get("binding") or "openai"),
            ).strip()
            or "openai"
        )
        default_base = str(current_profile.get("base_url") or "")
        display_provider = "Custom"
        env_key = ""

    edit_base = typer.confirm(strings["init.edit_base_url"], default=not bool(default_base))
    if edit_base:
        base_url = typer.prompt(strings["init.new_base_url"], default=default_base or "")
    else:
        base_url = default_base
        wiz.info(console, f"Base URL · {base_url or '(empty)'}")

    api_key = wiz.capture_api_key(
        console,
        strings,
        env_key=env_key,
        current=str(current_profile.get("api_key") or ""),
    )

    models = wiz.fetch_models(
        console,
        strings,
        base_url=base_url,
        api_key=api_key,
        binding=binding,
    )
    if not models:
        models = list(wiz.LLM_FALLBACK_MODELS.get(binding, ()))

    model = wiz.select_model(
        console,
        strings,
        models=models,
        current=str(current_model.get("model") or ""),
    )

    choice = wiz.LLMChoice(
        binding=binding,
        base_url=base_url,
        api_key=api_key,
        model=model,
        display_provider=display_provider,
    )

    if typer.confirm(strings["init.probe_offer"], default=True):
        _probe_llm_with_retry(console, strings, choice)

    return choice


def _probe_llm_with_retry(console: Console, strings: dict, choice: wiz.LLMChoice) -> None:
    """Run the probe; on failure, offer a single retry with a fresh API key."""
    while True:
        wiz.info(console, strings["init.probe_running"].format(what=choice.display_provider))
        ok_result, elapsed_ms, error = wiz.probe_llm(
            base_url=choice.base_url,
            api_key=choice.api_key,
            binding=choice.binding,
            model=choice.model,
        )
        choice.probed = True
        choice.probe_ok = ok_result
        choice.probe_ms = elapsed_ms
        if ok_result:
            wiz.ok(
                console,
                strings["init.probe_ok"].format(what=choice.display_provider, ms=elapsed_ms),
            )
            return
        wiz.fail(
            console, strings["init.probe_fail"].format(what=choice.display_provider, error=error)
        )
        if not typer.confirm(strings["init.probe_retry"], default=False):
            return
def _search_step(
    console: Console,
    strings: dict,
    catalog: dict,
) -> wiz.SearchChoice | None:
    """Returns ``None`` when the user picks ``[s] Skip``.

    A ``provider == "none"`` result is NOT skip — it's "explicitly disable
    web search". We still write that into the catalog so agents stop trying.
    """

    current_profile = (catalog.get("services", {}).get("search", {}).get("profiles") or [{}])[
        0
    ] or {}
    current_provider = str(current_profile.get("provider") or "tavily")

    spec = wiz.select_search_provider(console, strings, current=current_provider)
    if spec is None:
        wiz.info(console, strings["init.skipped"])
        return None

    if spec.name == "none":
        wiz.info(console, strings["init.search_disabled_note"])
        return wiz.SearchChoice(provider="none", label=spec.label)

    api_key = ""
    if spec.requires_api_key:
        env_key, env_name = wiz.search_api_key_from_env(spec.env_keys)
        if env_key:
            masked = wiz._mask_secret(env_key)
            offer = strings["init.api_key_env_detected"].format(env_var=env_name, masked=masked)
            if typer.confirm(offer, default=True):
                api_key = env_key
        if not api_key:
            current_key = str(current_profile.get("api_key") or "")
            if current_key:
                masked = wiz._mask_secret(current_key)
                if typer.confirm(
                    strings["init.api_key_reuse_llm"].format(masked=masked), default=True
                ):
                    api_key = current_key
        if not api_key:
            api_key = typer.prompt(
                strings["init.search_api_key_prompt"],
                default="",
                hide_input=True,
                show_default=False,
            )
    else:
        wiz.info(console, strings["init.search_no_key_note"].format(label=spec.label))

    base_url = ""
    if spec.requires_base_url:
        default_url = str(current_profile.get("base_url") or "") or spec.default_base_url
        base_url = typer.prompt(strings["init.search_base_url_prompt"], default=default_url)

    return wiz.SearchChoice(
        provider=spec.name,
        label=spec.label,
        api_key=api_key,
        base_url=base_url,
    )


def _ensure_search_service(catalog: dict, profile_id: str) -> dict:
    """Locate or create the default search profile we'll mutate in place."""
    services = catalog.setdefault("services", {})
    service = services.setdefault(
        "search",
        {"active_profile_id": profile_id, "profiles": []},
    )
    profiles = service.setdefault("profiles", [])
    profile = next(
        (item for item in profiles if item.get("id") == service.get("active_profile_id")), None
    )
    if profile is None:
        profile = {
            "id": profile_id,
            "name": "Default Search",
            "provider": "brave",
            "base_url": "",
            "api_key": "",
            "api_version": "",
            "extra_headers": {},
            "proxy": "",
            "models": [],
        }
        profiles.append(profile)
        service["active_profile_id"] = profile_id
    return profile


def run_init(*, cli_only: bool = False, home: str | Path | None = None) -> None:
    runtime_home = get_runtime_home(home)
    runtime_home.mkdir(parents=True, exist_ok=True)
    import os

    os.environ[KAGWEB_HOME_ENV] = str(runtime_home)
    _reset_runtime_singletons()

    from kagweb.runtime.banner import labels_for, print_banner, resolve_language
    from kagweb.services.config import get_model_catalog_service, get_runtime_settings_service
    from kagweb.services.setup import init_user_directories

    init_user_directories(runtime_home)

    language = resolve_language()
    strings = labels_for(language)
    console = Console()
    # CLI-only: LLM, Embedding, Search, Review = 4 steps.
    # Full:     Ports, LLM, Embedding, Search, Review = 5 steps.
    total_steps = 4 if cli_only else 5

    try:
        print_banner(console, language=language, mode_key="init.mode")
        console.print(f"{strings['init.workspace']}: [bold]{runtime_home}[/bold]")
        console.print(f"[dim]{strings['init.note_settings_dir']}[/dim]")

        runtime = get_runtime_settings_service()
        system = runtime.load_system(include_process_overrides=False)

        # --- Step 1 (CLI mode skips ports) ---
        step_num = 0
        if not cli_only:
            step_num += 1
            wiz.step_header(
                console,
                strings["init.step_ports"].format(n=step_num, total=total_steps),
            )
            system["backend_port"] = int(
                typer.prompt(
                    strings["init.backend_port"],
                    default=str(system.get("backend_port") or 8082),
                )
            )
            system["frontend_port"] = int(
                typer.prompt(
                    strings["init.frontend_port"],
                    default=str(system.get("frontend_port") or 8092),
                )
            )

        # --- Step 2: LLM ---
        catalog_service = get_model_catalog_service()
        catalog = catalog_service.load()
        llm_profile, llm_model = _ensure_model_service(
            catalog, "llm", "llm-profile-default", "llm-model-default"
        )
        step_num += 1
        wiz.step_header(console, strings["init.step_llm"].format(n=step_num, total=total_steps))
        llm_choice = _llm_step(console, strings, llm_profile, llm_model)

        # Apply LLM choice back into the catalog draft.
        llm_profile["binding"] = llm_choice.binding
        llm_profile["base_url"] = llm_choice.base_url
        llm_profile["api_key"] = llm_choice.api_key
        llm_model["model"] = llm_choice.model
        llm_model["name"] = llm_choice.model or "Default Model"

        # --- Step 3: Search (skip via [s] inside the picker) ---

        search_choice: wiz.SearchChoice | None = None
        step_num += 1
        wiz.step_header(console, strings["init.step_search"].format(n=step_num, total=total_steps))
        search_choice = _search_step(console, strings, catalog)
        if search_choice is not None:
            search_profile = _ensure_search_service(catalog, "search-profile-default")
            search_profile["provider"] = search_choice.provider
            search_profile["api_key"] = search_choice.api_key
            search_profile["base_url"] = search_choice.base_url

        # --- Step 5: Review & save ---
        step_num += 1
        wiz.step_header(console, strings["init.step_review"].format(n=step_num, total=total_steps))
        wiz.render_review_panel(
            console,
            strings,
            llm=llm_choice,
            search=search_choice,
            backend_port=None if cli_only else system.get("backend_port"),
            frontend_port=None if cli_only else system.get("frontend_port"),
        )
        if not typer.confirm(strings["init.confirm_save"], default=True):
            wiz.warn(console, strings["init.cancelled"])
            raise typer.Exit(code=1)

        if not cli_only:
            runtime.save_system(system)
        catalog_service.save(catalog)
        console.print()
        wiz.ok(console, strings["init.saved"])
        console.print(f"[dim]{strings['init.next_step']}[/dim]")

    except (KeyboardInterrupt, typer.Abort):
        console.print()
        wiz.warn(console, strings["init.cancelled"])
        raise typer.Exit(code=130)


def register(app: typer.Typer) -> None:
    @app.command("init")
    def init_command(
        cli: bool = typer.Option(False, "--cli", help="Initialize for CLI-only use."),
        home: Path | None = typer.Option(None, "--home", help="Runtime workspace root."),
    ) -> None:
        """Create or update data/user/settings for this workspace."""

        run_init(cli_only=cli, home=home)
