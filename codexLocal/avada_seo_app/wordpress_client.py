import mimetypes
import tempfile
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import requests
from requests.auth import HTTPBasicAuth


@dataclass
class WordPressCredentials:
    site_url: str
    username: str
    application_password: str


class WordPressClient:
    def __init__(self, creds: WordPressCredentials, timeout_sec: int = 120) -> None:
        base = creds.site_url.strip().rstrip("/")
        if base and not base.startswith("http"):
            base = "https://" + base
        self.base_url = base
        self.auth = HTTPBasicAuth(creds.username.strip(), creds.application_password.strip())
        self.timeout = max(30, int(timeout_sec))

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def check_auth(self) -> Dict[str, object]:
        response = requests.get(
            self._endpoint("/wp-json/wp/v2/users/me?context=edit"),
            auth=self.auth,
            timeout=(20, self.timeout),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"WordPress auth failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        return {"ok": True, "user_id": data.get("id"), "name": data.get("name", "")}

    def _check_endpoint_read(self, path: str) -> Dict[str, object]:
        response = requests.get(
            self._endpoint(path),
            auth=self.auth,
            timeout=(20, self.timeout),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Endpoint {path} failed ({response.status_code}): {response.text[:250]}")
        payload = response.json()
        count = len(payload) if isinstance(payload, list) else 1
        return {"path": path, "ok": True, "count": count}

    def delete_media(self, media_id: int, force: bool = True) -> Dict[str, object]:
        suffix = "?force=true" if force else ""
        response = requests.delete(
            self._endpoint(f"/wp-json/wp/v2/media/{int(media_id)}{suffix}"),
            auth=self.auth,
            timeout=(20, self.timeout),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Delete media failed ({response.status_code}): {response.text[:250]}")
        return {"ok": True, "media_id": int(media_id)}

    def test_connection_full(self) -> Dict[str, object]:
        user = self.check_auth()
        pages = self._check_endpoint_read("/wp-json/wp/v2/pages?per_page=1")
        posts = self._check_endpoint_read("/wp-json/wp/v2/posts?per_page=1")

        png_1x1 = b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pAq4AAAAASUVORK5CYII="
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(png_1x1)
            test_path = tmp.name
        uploaded = self.upload_media(
            local_path=test_path,
            upload_filename="connection-test-codex",
            title="Connection Test Codex",
            alt_text="Connection test image",
            caption="Connection test",
            description="Temporary file uploaded for WordPress API connection test.",
        )
        media_id = int(uploaded.get("media_id", 0))
        cleanup = {"ok": False}
        if media_id:
            try:
                cleanup = self.delete_media(media_id, force=True)
            except Exception as exc:
                cleanup = {"ok": False, "error": str(exc), "media_id": media_id}

        try:
            Path(test_path).unlink(missing_ok=True)
        except Exception:
            pass

        return {
            "ok": True,
            "user": user,
            "pages": pages,
            "posts": posts,
            "upload_test": uploaded,
            "cleanup": cleanup,
        }

    def upload_media(
        self,
        local_path: str,
        upload_filename: str,
        title: str,
        alt_text: str,
        caption: str,
        description: str,
    ) -> Dict[str, object]:
        src = Path(local_path)
        if not src.exists():
            raise FileNotFoundError(f"Nie znaleziono pliku: {src}")

        ext = src.suffix.lower() or ".jpg"
        fname = (upload_filename or src.stem).strip()
        if fname.lower().endswith(ext):
            upload_name = fname
        else:
            upload_name = f"{fname}{ext}"

        mime = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        payload = src.read_bytes()
        headers = {
            "Content-Disposition": f'attachment; filename="{upload_name}"',
            "Content-Type": mime,
        }
        upload_resp = requests.post(
            self._endpoint("/wp-json/wp/v2/media"),
            auth=self.auth,
            data=payload,
            headers=headers,
            timeout=(20, self.timeout),
        )
        if upload_resp.status_code >= 400:
            raise RuntimeError(f"Upload failed ({upload_resp.status_code}): {upload_resp.text[:300]}")
        media = upload_resp.json()
        media_id = int(media.get("id"))

        meta_payload = {
            "title": title.strip() or Path(upload_name).stem,
            "alt_text": alt_text.strip(),
            "caption": caption.strip(),
            "description": description.strip(),
        }
        meta_resp = requests.post(
            self._endpoint(f"/wp-json/wp/v2/media/{media_id}"),
            auth=self.auth,
            json=meta_payload,
            timeout=(20, self.timeout),
        )
        if meta_resp.status_code >= 400:
            raise RuntimeError(f"Meta update failed ({meta_resp.status_code}): {meta_resp.text[:300]}")
        media_updated = meta_resp.json()
        return {
            "media_id": media_id,
            "source_url": str(media_updated.get("source_url", media.get("source_url", ""))),
            "local_path": str(src),
            "upload_filename": upload_name,
            "title": meta_payload["title"],
            "alt_text": meta_payload["alt_text"],
            "caption": meta_payload["caption"],
            "description": meta_payload["description"],
        }

    def _detect_portfolio_rest_base(self) -> str:
        response = requests.get(
            self._endpoint("/wp-json/wp/v2/types"),
            auth=self.auth,
            timeout=(20, self.timeout),
        )
        if response.status_code >= 400:
            return "portfolio"
        data = response.json()
        if not isinstance(data, dict):
            return "portfolio"
        candidates: list[str] = []
        for _, meta in data.items():
            if not isinstance(meta, dict):
                continue
            rest_base = str(meta.get("rest_base", "")).strip()
            slug = str(meta.get("slug", "")).strip().lower()
            name = str(meta.get("name", "")).strip().lower()
            if "portfolio" in rest_base.lower() or "portfolio" in slug or "portfolio" in name:
                candidates.append(rest_base)
        for preferred in ("portfolio", "avada_portfolio", "fusion_portfolio"):
            for c in candidates:
                if c.lower() == preferred:
                    return c
        return candidates[0] if candidates else "portfolio"

    def publish_content(
        self,
        title: str,
        content: str,
        status: str = "draft",
        target_type: str = "page",
        slug: str = "",
        featured_media_id: int = 0,
        yoast_focus_keyphrase: str = "",
        yoast_meta_description: str = "",
    ) -> Dict[str, object]:
        t = target_type.strip().lower()
        if t == "page":
            endpoint = "/wp-json/wp/v2/pages"
            effective_type = "page"
        else:
            rest_base = self._detect_portfolio_rest_base()
            endpoint = f"/wp-json/wp/v2/{rest_base}"
            effective_type = rest_base

        payload = {
            "title": title.strip() or "Generated AVADA Page",
            "content": content,
            "status": "publish" if status.strip().lower() == "publish" else "draft",
        }
        if int(featured_media_id) > 0:
            payload["featured_media"] = int(featured_media_id)
        if slug.strip():
            payload["slug"] = slug.strip()

        response = requests.post(
            self._endpoint(endpoint),
            auth=self.auth,
            json=payload,
            timeout=(20, self.timeout),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Publish failed ({response.status_code}): {response.text[:350]}")
        data = response.json()

        yoast_saved = False
        yoast_error = ""
        meta_patch: Dict[str, object] = {}
        if yoast_focus_keyphrase.strip():
            meta_patch["yoast_wpseo_focuskw"] = yoast_focus_keyphrase.strip()
        if yoast_meta_description.strip():
            meta_patch["yoast_wpseo_metadesc"] = yoast_meta_description.strip()
        if meta_patch:
            patch_resp = requests.post(
                self._endpoint(f"{endpoint}/{int(data.get('id', 0))}"),
                auth=self.auth,
                json={"meta": meta_patch},
                timeout=(20, self.timeout),
            )
            if patch_resp.status_code < 400:
                yoast_saved = True
            else:
                yoast_error = patch_resp.text[:300]

        return {
            "id": int(data.get("id", 0)),
            "status": str(data.get("status", "")),
            "link": str(data.get("link", "")),
            "type": effective_type,
            "slug": str(data.get("slug", "")),
            "featured_media": int(data.get("featured_media", 0) or 0),
            "yoast_saved": yoast_saved,
            "yoast_error": yoast_error,
        }
