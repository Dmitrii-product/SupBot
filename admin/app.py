"""Admin web UI for managing shops and catalogs.

Start:
    uvicorn admin.app:app --host 0.0.0.0 --port 8080

On a public server, put nginx/Caddy in front and terminate TLS there.
Language is auto-detected from the browser's Accept-Language header;
users can override it with the EN/RU/UZ switcher saved as a cookie.
"""
import asyncio
import html as _html
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from admin import i18n
from bot import store

_SESSION_COOKIE = "sb_admin"
_LANG_COOKIE = "sb_lang"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class _NotAuthenticated(Exception):
    pass


@app.exception_handler(_NotAuthenticated)
async def _not_auth_handler(request: Request, exc: _NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


def _get_username(sb_admin: str | None = Cookie(default=None)) -> str | None:
    return store.get_session(sb_admin) if sb_admin else None


def _require_auth(sb_admin: str | None = Cookie(default=None)) -> str:
    username = store.get_session(sb_admin) if sb_admin else None
    if not username:
        raise _NotAuthenticated()
    return username


# ---------------------------------------------------------------------------
# Locale
# ---------------------------------------------------------------------------

def get_locale(
    sb_lang: str | None = Cookie(default=None),
    accept_language: str | None = Header(default=None),
) -> str:
    if sb_lang in i18n.SUPPORTED:
        return sb_lang
    return i18n.detect(accept_language)


@app.get("/lang")
async def set_lang(code: str = "en", next: str = "/"):
    if code not in i18n.SUPPORTED:
        code = "en"
    resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
    resp.set_cookie(_LANG_COOKIE, code, httponly=True, samesite="lax", max_age=365 * 86400)
    return resp


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _e(v) -> str:
    return _html.escape(str(v or ""))


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")


def _page(
    title: str,
    body: str,
    *,
    locale: str = "en",
    flash: str = "",
    flash_ok: bool = True,
    username: str | None = None,
    current_path: str = "/",
) -> HTMLResponse:
    t = i18n.T[locale]
    alert = ""
    if flash:
        cls = "success" if flash_ok else "danger"
        alert = (
            f'<div class="alert alert-{cls} alert-dismissible fade show" role="alert">'
            f"{_e(flash)}"
            f'<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'
        )

    lang_links = " ".join(
        f'<a href="/lang?code={code}&next={quote_plus(current_path)}" '
        f'class="text-white-50 small{"" if locale != code else " fw-bold text-white"}" '
        f'style="text-decoration:none">{t["lang_" + code]}</a>'
        for code in i18n.SUPPORTED
    )

    nav_right = f'<div class="d-flex align-items-center gap-3">{lang_links}</div>'
    if username:
        nav_right = f"""
<div class="d-flex align-items-center gap-3">
  {lang_links}
  <span class="text-white-50 small">|</span>
  <span class="text-white-50 small">{_e(username)}</span>
  <form method="post" action="/logout" class="mb-0">
    <button type="submit" class="btn btn-sm btn-outline-light">{t["logout"]}</button>
  </form>
</div>"""

    return HTMLResponse(f"""<!doctype html>
<html lang="{locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} — SupBot Admin</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-dark bg-dark mb-4">
  <div class="container-lg d-flex justify-content-between align-items-center">
    <a class="navbar-brand fw-bold" href="/">{t["brand"]}</a>
    {nav_right}
  </div>
</nav>
<div class="container-lg pb-5" style="max-width:900px">
  {alert}
  <h5 class="mb-3">{_e(title)}</h5>
  {body}
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>""")


# ---------------------------------------------------------------------------
# Login / Register / Logout
# ---------------------------------------------------------------------------

@app.get("/login")
async def login_form(
    msg: str = "",
    ok: int = 1,
    username: str | None = Depends(_get_username),
    locale: str = Depends(get_locale),
):
    if username:
        return RedirectResponse("/", status_code=303)
    t = i18n.T[locale]
    body = f"""
<div class="card mx-auto" style="max-width:420px">
  <div class="card-body p-4">
    <form method="post" action="/login">
      <div class="mb-3">
        <label class="form-label fw-semibold">{t["login_username"]}</label>
        <input name="username" class="form-control" required autofocus autocomplete="username">
      </div>
      <div class="mb-4">
        <label class="form-label fw-semibold">{t["login_password"]}</label>
        <input name="password" type="password" class="form-control" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary w-100">{t["login_submit"]}</button>
    </form>
    <hr class="my-3">
    <p class="text-center text-muted small mb-0">
      {t["login_no_account"]} <a href="/register">{t["login_create_link"]}</a>
    </p>
  </div>
</div>"""
    return _page(t["login_title"], body, locale=locale, flash=msg, flash_ok=bool(ok), current_path="/login")


@app.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    locale: str = Depends(get_locale),
):
    if not store.verify_admin(username, password):
        t = i18n.T[locale]
        return RedirectResponse(
            f"/login?msg={quote_plus(t['login_invalid'])}&ok=0", status_code=303
        )
    token = store.create_session(username)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=2_592_000)
    return resp


@app.get("/register")
async def register_form(
    msg: str = "",
    ok: int = 1,
    username: str | None = Depends(_get_username),
    locale: str = Depends(get_locale),
):
    if store.admin_count() > 0 and not username:
        return RedirectResponse("/login", status_code=303)
    t = i18n.T[locale]
    first_run_banner = (
        f'<div class="alert alert-info py-2 small mb-3">{t["register_first_run"]}</div>'
        if store.admin_count() == 0
        else ""
    )
    body = f"""
<div class="card mx-auto" style="max-width:420px">
  <div class="card-body p-4">
    {first_run_banner}
    <form method="post" action="/register">
      <div class="mb-3">
        <label class="form-label fw-semibold">{t["register_username"]}</label>
        <input name="new_username" class="form-control" required autofocus autocomplete="username">
      </div>
      <div class="mb-3">
        <label class="form-label fw-semibold">{t["register_password"]}</label>
        <input name="password" type="password" class="form-control" required autocomplete="new-password">
        <div class="form-text">{t["register_password_hint"]}</div>
      </div>
      <div class="mb-4">
        <label class="form-label fw-semibold">{t["register_confirm"]}</label>
        <input name="confirm" type="password" class="form-control" required autocomplete="new-password">
      </div>
      <button type="submit" class="btn btn-primary w-100">{t["register_submit"]}</button>
    </form>
    <hr class="my-3">
    <p class="text-center text-muted small mb-0">
      {t["register_have_account"]} <a href="/login">{t["register_signin_link"]}</a>
    </p>
  </div>
</div>"""
    return _page(
        t["register_title"], body,
        locale=locale, flash=msg, flash_ok=bool(ok),
        username=username, current_path="/register",
    )


@app.post("/register")
async def register(
    new_username: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    username: str | None = Depends(_get_username),
    locale: str = Depends(get_locale),
):
    if store.admin_count() > 0 and not username:
        return RedirectResponse("/login", status_code=303)
    t = i18n.T[locale]

    def _redir(key: str) -> RedirectResponse:
        return RedirectResponse(
            f"/register?msg={quote_plus(t[key])}&ok=0", status_code=303
        )

    if not new_username.strip():
        return _redir("register_err_empty")
    if len(password) < 8:
        return _redir("register_err_short")
    if password != confirm:
        return _redir("register_err_mismatch")
    if not store.create_admin(new_username.strip(), password):
        return _redir("register_err_taken")

    if username:
        return RedirectResponse(
            f"/?msg={quote_plus(t['register_ok'])}", status_code=303
        )
    token = store.create_session(new_username.strip())
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=2_592_000)
    return resp


@app.post("/logout")
async def logout(sb_admin: str | None = Cookie(default=None)):
    if sb_admin:
        store.delete_session(sb_admin)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Shop list
# ---------------------------------------------------------------------------

@app.get("/")
async def shop_list(
    msg: str = "",
    ok: int = 1,
    username: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    shops = store.get_all_shops()
    rows = ""
    for s in shops:
        n = store.catalog_count(s["shop_id"])
        esc = len(store.get_escalated_contacts(s["shop_id"]))
        esc_badge = (
            f'<span class="badge bg-warning text-dark">{t["shops_waiting"].format(n=esc)}</span>'
            if esc
            else '<span class="badge bg-success">0</span>'
        )
        rows += f"""
<tr>
  <td>
    <strong>{_e(s["name"])}</strong><br>
    <small class="text-muted">{_e(s["shop_id"])}</small>
  </td>
  <td><code class="small">{_e(s["bot_token"][:28])}…</code></td>
  <td>{t["chunks"].format(n=n)}</td>
  <td>{esc_badge}</td>
  <td>
    <a href="/shops/{_e(s["shop_id"])}" class="btn btn-sm btn-outline-primary">{t["shops_manage"]}</a>
  </td>
</tr>"""

    count_key = "shops_count_1" if len(shops) == 1 else "shops_count_n"
    empty = (
        f'<tr><td colspan="5" class="text-center text-muted py-4">{t["shops_empty"]}</td></tr>'
    )
    body = f"""
<div class="d-flex justify-content-between align-items-center mb-3">
  <span class="text-muted">{t[count_key].format(n=len(shops))}</span>
  <a href="/shops/new" class="btn btn-primary">{t["shops_add"]}</a>
</div>
<div class="card">
  <table class="table table-bordered mb-0">
    <thead class="table-light">
      <tr>
        <th>{t["col_shop"]}</th>
        <th>{t["col_token"]}</th>
        <th>{t["col_catalog"]}</th>
        <th>{t["col_escalations"]}</th>
        <th></th>
      </tr>
    </thead>
    <tbody>{rows or empty}</tbody>
  </table>
</div>
<div class="alert alert-info mt-4">{t["shops_restart"]}</div>"""
    return _page(t["shops_title"], body, locale=locale, flash=msg, flash_ok=bool(ok), username=username)


# ---------------------------------------------------------------------------
# Add shop
# ---------------------------------------------------------------------------

@app.get("/shops/new")
async def new_shop_form(
    msg: str = "",
    ok: int = 1,
    username: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    body = f"""
<div class="card" style="max-width:560px">
  <div class="card-body">
    <form method="post" action="/shops">
      <div class="mb-3">
        <label class="form-label fw-semibold">{t["new_shop_name"]}</label>
        <input name="name" class="form-control" required placeholder="{_e(t['new_shop_name_ph'])}">
      </div>
      <div class="mb-3">
        <label class="form-label fw-semibold">{t["new_shop_token"]}</label>
        <input name="bot_token" class="form-control font-monospace" required
               placeholder="{_e(t['new_shop_token_ph'])}">
        <div class="form-text">{t["new_shop_token_hint"]}</div>
      </div>
      <div class="mb-4">
        <label class="form-label fw-semibold">{t["new_shop_manager"]}</label>
        <input name="manager_chat_id" class="form-control" type="number" required
               placeholder="{_e(t['new_shop_manager_ph'])}">
        <div class="form-text">{t["new_shop_manager_hint"]}</div>
      </div>
      <a href="/" class="btn btn-secondary me-2">{t["cancel"]}</a>
      <button type="submit" class="btn btn-primary">{t["new_shop_submit"]}</button>
    </form>
  </div>
</div>"""
    return _page(t["new_shop_title"], body, locale=locale, flash=msg, flash_ok=bool(ok), username=username, current_path="/shops/new")


@app.post("/shops")
async def create_shop(
    name: str = Form(...),
    bot_token: str = Form(...),
    manager_chat_id: int = Form(...),
    username: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    shop_id = _slugify(name)
    if not shop_id:
        return RedirectResponse(
            f"/shops/new?msg={quote_plus(t['new_shop_err_name'])}&ok=0", status_code=303
        )
    store.upsert_shop(shop_id, name.strip(), bot_token.strip(), manager_chat_id)
    return RedirectResponse(
        f"/shops/{shop_id}?msg={quote_plus(t['new_shop_created'])}", status_code=303
    )


# ---------------------------------------------------------------------------
# Shop detail
# ---------------------------------------------------------------------------

@app.get("/shops/{shop_id}")
async def shop_detail(
    shop_id: str,
    msg: str = "",
    ok: int = 1,
    username: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    shop = store.get_shop(shop_id)
    if not shop:
        raise HTTPException(404, "Shop not found")
    n = store.catalog_count(shop_id)

    body = f"""
<div class="row g-4">
  <div class="col-md-5">
    <div class="card h-100">
      <div class="card-header fw-semibold">{t["shop_info"]}</div>
      <div class="card-body">
        <dl class="row mb-0">
          <dt class="col-5">{t["shop_id"]}</dt>
          <dd class="col-7"><code>{_e(shop_id)}</code></dd>
          <dt class="col-5">{t["shop_name"]}</dt>
          <dd class="col-7">{_e(shop["name"])}</dd>
          <dt class="col-5">{t["shop_token"]}</dt>
          <dd class="col-7"><code class="small">{_e(shop["bot_token"][:32])}…</code></dd>
          <dt class="col-5">{t["shop_manager"]}</dt>
          <dd class="col-7"><code>{_e(shop["manager_chat_id"])}</code></dd>
          <dt class="col-5">{t["shop_catalog"]}</dt>
          <dd class="col-7"><strong>{n}</strong> {t["chunks"].format(n="").strip()}</dd>
        </dl>
      </div>
    </div>
  </div>

  <div class="col-md-7 d-flex flex-column gap-3">
    <div class="card">
      <div class="card-header fw-semibold">{t["upload_title"]}</div>
      <div class="card-body">
        <form method="post" action="/shops/{_e(shop_id)}/catalog"
              enctype="multipart/form-data">
          <div class="mb-3">
            <input name="file" type="file" accept=".csv" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label fw-semibold small">{t["upload_mode_label"]}</label>
            <div class="d-flex flex-column gap-2">
              <div class="form-check">
                <input class="form-check-input" type="radio" name="mode"
                       id="mode_rewrite" value="rewrite" checked>
                <label class="form-check-label" for="mode_rewrite">
                  <strong>{t["upload_mode_rewrite"]}</strong>
                  <span class="text-muted d-block" style="font-size:.85em">{t["upload_mode_rewrite_hint"]}</span>
                </label>
              </div>
              <div class="form-check">
                <input class="form-check-input" type="radio" name="mode"
                       id="mode_update" value="update">
                <label class="form-check-label" for="mode_update">
                  <strong>{t["upload_mode_update"]}</strong>
                  <span class="text-muted d-block" style="font-size:.85em">{t["upload_mode_update_hint"]}</span>
                </label>
              </div>
            </div>
          </div>
          <p class="text-muted small mb-3">
            {t["upload_hint"]}
            <a href="/sample-catalog">{t["upload_sample"]}</a>
          </p>
          <button type="submit" class="btn btn-primary">{t["upload_submit"]}</button>
        </form>
      </div>
    </div>

    <div class="card border-danger">
      <div class="card-header text-danger fw-semibold">{t["danger_title"]}</div>
      <div class="card-body">
        <p class="small text-muted mb-2">{t["danger_text"]}</p>
        <form method="post" action="/shops/{_e(shop_id)}/delete"
              onsubmit="return confirm('{_e(t['delete_confirm'].format(name=shop['name']))}')">
          <button type="submit" class="btn btn-outline-danger btn-sm">{t["delete_submit"]}</button>
        </form>
      </div>
    </div>
  </div>
</div>

<div class="mt-4"><a href="/">{t["back"]}</a></div>"""
    return _page(
        f'{t["shop_info"]}: {shop["name"]}', body,
        locale=locale, flash=msg, flash_ok=bool(ok),
        username=username, current_path=f"/shops/{shop_id}",
    )


# ---------------------------------------------------------------------------
# Catalog upload
# ---------------------------------------------------------------------------

@app.get("/sample-catalog")
async def sample_catalog(_: str = Depends(_require_auth)):
    from fastapi.responses import Response
    csv_path = Path(__file__).parent.parent / "data" / "sample_catalog.csv"
    return Response(
        content=csv_path.read_bytes(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sample_catalog.csv"},
    )


@app.post("/shops/{shop_id}/catalog")
async def upload_catalog(
    shop_id: str,
    file: UploadFile = File(...),
    mode: str = Form(default="rewrite"),
    _: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    if not store.get_shop(shop_id):
        raise HTTPException(404)
    if mode not in ("rewrite", "update"):
        mode = "rewrite"
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from bot.ingest import extract_key, rows_to_chunks, rows_to_records
        chunks = await asyncio.to_thread(rows_to_chunks, tmp_path)
        if not chunks:
            return RedirectResponse(
                f"/shops/{shop_id}?msg={quote_plus(t['upload_err'])}&ok=0", status_code=303
            )

        if mode == "update":
            records = await asyncio.to_thread(rows_to_records, tmp_path)
            id_field = next(iter(records[0])) if records else None
            if id_field:
                new_ids = {r.get(id_field, "").strip() for r in records if r.get(id_field, "").strip()}
                existing = await asyncio.to_thread(store.get_all_items, shop_id)
                rowids_to_delete = [
                    rowid for rowid, chunk in existing
                    if extract_key(chunk, id_field) in new_ids
                ]
                if rowids_to_delete:
                    await asyncio.to_thread(store.delete_items, shop_id, rowids_to_delete)
        else:
            await asyncio.to_thread(store.clear_shop, shop_id)

        n = await asyncio.to_thread(store.add_items, shop_id, chunks)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return RedirectResponse(
        f"/shops/{shop_id}?msg={quote_plus(t['upload_ok'].format(n=n))}", status_code=303
    )


# ---------------------------------------------------------------------------
# Delete shop
# ---------------------------------------------------------------------------

@app.post("/shops/{shop_id}/delete")
async def delete_shop_route(
    shop_id: str,
    _: str = Depends(_require_auth),
    locale: str = Depends(get_locale),
):
    t = i18n.T[locale]
    shop = store.get_shop(shop_id)
    if not shop:
        raise HTTPException(404)
    name = shop["name"]
    store.delete_shop(shop_id)
    return RedirectResponse(
        f"/?msg={quote_plus(t['shop_deleted'].format(name=name))}", status_code=303
    )
