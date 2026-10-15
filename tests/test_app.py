import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET


class LanServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"RIIRIITUBE_DATA": self.temp.name}):
            spec = importlib.util.spec_from_file_location(
                "riiriitube_test_app", Path(__file__).parents[1] / "app.py"
            )
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        self.vid = "jNQXAC9IVRw"

    def tearDown(self):
        self.temp.cleanup()

    def ready_file(self):
        folder = Path(self.temp.name) / self.vid
        folder.mkdir(exist_ok=True)
        path = folder / "video.mp4"
        path.write_bytes(bytes(range(256)) * 32)
        self.module.jobs[self.vid] = {"id": self.vid, "title": "Test", "status": "ready"}
        return path

    def test_range_and_head_for_mplayer_seeking(self):
        path = self.ready_file()
        full = path.read_bytes()
        url = f"/media/{self.vid}.mp4"
        with self.client.get(url) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.data, full)
        with self.client.head(url) as response:
            self.assertEqual(response.headers["Content-Length"], str(len(full)))
            self.assertEqual(response.data, b"")
        for header, expected in [
            ("bytes=100-199", full[100:200]),
            ("bytes=-100", full[-100:]),
            ("bytes=8000-", full[8000:]),
        ]:
            with self.client.get(url, headers={"Range": header}) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.data, expected)
                self.assertIn("Content-Range", response.headers)
        self.assertEqual(self.client.get(url, headers={"Range": "bytes=999999-"}).status_code, 416)

    def test_file_over_four_gigabytes(self):
        path = self.ready_file()
        size = 5 * 1024**3
        with path.open("r+b") as file:
            file.truncate(size)
            file.seek(size - 4)
            file.write(b"tail")
        url = f"/media/{self.vid}.mp4"
        with self.client.head(url) as response:
            self.assertEqual(int(response.headers["Content-Length"]), size)
        with self.client.get(url, headers={"Range": "bytes=-4"}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b"tail")

    def test_partial_video_is_not_published(self):
        self.ready_file()
        self.module.jobs[self.vid]["status"] = "converting"
        self.assertEqual(self.client.get(f"/media/{self.vid}.mp4").status_code, 404)

    def test_wii_variants_preserve_browser_mp4_and_support_ranges(self):
        path = self.ready_file()
        path.with_suffix(".mkv").write_bytes(b"matroska-test")
        path.with_suffix(".ts").write_bytes(b"mpeg-test")
        playlist = self.client.get(f"/wiimc/video/{self.vid}.plx").text
        self.assertIn(f"/media/{self.vid}.mkv", playlist)
        self.assertIn(f"/media/{self.vid}.ts", playlist)
        self.assertNotIn(f"/media/{self.vid}.mp4", playlist)
        for ext in ("mkv", "ts", "mp4"):
            with self.client.get(
                f"/media/{self.vid}.{ext}", headers={"Range": "bytes=0-3"}
            ) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(len(response.data), 4)
        self.assertEqual(self.client.get(f"/media/{self.vid}.json").status_code, 404)
        path.with_suffix(".mkv").unlink()
        self.assertEqual(self.client.get(f"/media/{self.vid}.mkv").status_code, 404)

    def test_url_validation(self):
        for value in [
            self.vid,
            f"https://youtu.be/{self.vid}?t=2",
            f"https://www.youtube.com/watch?v={self.vid}",
        ]:
            self.assertEqual(self.module.video_id(value), self.vid)
        for value in [
            "../../etc/passwd",
            "http://localhost/admin",
            f"https://youtube.com.evil.test/watch?v={self.vid}",
        ]:
            with self.assertRaises(ValueError):
                self.module.video_id(value)

    def test_only_creator_english_captions_are_selected(self):
        select = self.module.manual_english_track
        auto = {"en": [{"ext": "srt"}], "en-orig": [{"ext": "vtt"}]}
        self.assertIsNone(select({"automatic_captions": auto}))
        self.assertIsNone(select({"subtitles": {"fr": [{}]}, "automatic_captions": auto}))
        self.assertEqual(
            select({"subtitles": {"en-GB": [{}]}, "automatic_captions": auto}), "en-GB"
        )
        self.assertEqual(select({"subtitles": {"en-US": [{}], "en": [{}]}}), "en")
        self.assertIsNone(select({"subtitles": {"en": [], "english": [{}]}}))

    def test_no_manual_captions_clears_stale_subtitles_without_download(self):
        folder = Path(self.temp.name)
        (folder / "english.srt").write_text("stale captions")
        with patch.object(self.module, "run_logged") as run:
            result = self.module.download_captions(
                {"automatic_captions": {"en": [{}]}}, folder / "info.json", folder, folder / "log"
            )
        self.assertIsNone(result)
        self.assertFalse((folder / "english.srt").exists())
        run.assert_not_called()

    def test_missing_required_mkv_is_not_ready_after_restart(self):
        self.ready_file()
        self.module.patch_job(self.vid, wii_format="mkv")
        self.module.jobs.clear()
        self.module.load_jobs()
        self.assertEqual(self.module.jobs[self.vid]["status"], "error")

    def test_playlist_escape_and_local_links(self):
        with patch.object(
            self.module,
            "search_page",
            return_value=([{"id": self.vid, "title": "hello\ntype=video\nURL=evil"}], False),
        ):
            response = self.client.get("/wiimc/search.plx?q=test")
        text = response.text
        self.assertEqual(text.count("\ntype="), 2)
        self.assertNotIn("\nURL=evil", text)
        self.assertIn(f"thumb={self.module.BASE}/thumb/{self.vid}.jpg", text)
        self.assertIn(f"URL={self.module.BASE}/wiimc/video/{self.vid}.plx", text)
        root = ET.fromstring(self.client.get("/onlinemedia.xml").data)
        folder = root.find("folder")
        self.assertEqual(folder.attrib["name"], "RiiTube at Home")
        self.assertEqual([link.attrib["type"] for link in folder], ["search", "playlist"])

    def search_result(self, count):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "entries": [
                        {"id": f"{number:011d}", "title": f"Result {number}"}
                        for number in range(count)
                    ]
                }
            ),
        )

    def test_search_pages_fetch_only_the_window_and_lookahead(self):
        with patch.object(
            self.module.subprocess, "run", return_value=self.search_result(13)
        ) as run:
            results, has_next = self.module.search_page("cats", 2)
        self.assertEqual(len(results), 12)
        self.assertTrue(has_next)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--playlist-start") + 1], "13")
        self.assertEqual(command[command.index("--playlist-end") + 1], "25")
        self.assertEqual(command[-1], "ytsearch25:cats")

    def test_search_cache_separates_pages_and_expires(self):
        with patch.object(
            self.module.subprocess,
            "run",
            side_effect=[self.search_result(13), self.search_result(3), self.search_result(2)],
        ) as run:
            first = self.module.search_page("cats", 1)
            second = self.module.search_page("cats", 2)
            self.assertEqual(self.module.search("cats"), first[0])
            self.assertEqual(self.module.search_page("cats", 2), second)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(first[1])
            self.assertFalse(second[1])
            self.module.search_cache[("cats", 2)] = (self.module.time.monotonic() - 301, second)
            self.assertEqual(len(self.module.search_page("cats", 2)[0]), 2)
            self.assertEqual(run.call_count, 3)

    def test_search_pages_end_without_phantom_next_page(self):
        for count in (0, 3, 12):
            with (
                self.subTest(count=count),
                patch.object(self.module.subprocess, "run", return_value=self.search_result(count)),
            ):
                results, has_next = self.module.search_page(f"count {count}")
            self.assertEqual(len(results), count)
            self.assertFalse(has_next)

    def test_direct_video_search_does_not_repeat_across_pages(self):
        with patch.object(self.module.subprocess, "run") as run:
            self.assertEqual(self.module.search(self.vid)[0]["id"], self.vid)
            self.assertEqual(self.module.search_page(self.vid, 2), ([], False))
        run.assert_not_called()

    def test_wii_page_links_keep_the_query_and_can_be_followed(self):
        query = "cats & dogs + # café /?"
        pages = [
            ([{"id": self.vid, "title": "First"}], True),
            ([{"id": "roBkg-iPrbw", "title": "Second"}], False),
        ]
        with patch.object(self.module, "search_page", side_effect=pages) as search:
            first = self.client.get("/wiimc/search.plx", query_string={"q": query}).text
            self.assertNotIn("Previous page", first)
            next_url = next(
                line[4:]
                for line in first.splitlines()
                if line.startswith("URL=") and "page=2" in line
            )
            self.assertEqual(parse_qs(urlsplit(next_url).query), {"q": [query], "page": ["2"]})
            second = self.client.get(urlsplit(next_url).path + "?" + urlsplit(next_url).query).text
        self.assertEqual(search.call_args_list[1].args, (query, 2))
        self.assertIn("Previous page (1)", second)
        self.assertNotIn("Next page", second)
        self.assertIn("name=New search", second)
        self.assertIn("/wiimc/video/roBkg-iPrbw.plx", second)

    def test_empty_or_failed_later_page_retains_navigation(self):
        with patch.object(self.module, "search_page", return_value=([], False)):
            text = self.client.get("/wiimc/search.plx?q=cats&page=3").text
        self.assertIn("Previous page (2)", text)
        self.assertIn("No results", text)
        self.assertNotIn("Next page", text)
        for error in (
            RuntimeError("Try again"),
            self.module.subprocess.TimeoutExpired("yt-dlp", 50),
        ):
            with patch.object(self.module, "search_page", side_effect=error):
                text = self.client.get("/wiimc/search.plx?q=cats&page=3").text
            self.assertIn("Previous page (2)", text)
            self.assertIn("select to retry", text)
            self.assertIn("q=cats&page=3", text)

    def test_invalid_pages_do_not_start_a_search(self):
        with patch.object(self.module, "search_page") as search:
            for page in ("0", "-1", "oops", "1.5", ""):
                self.assertIn(
                    "Invalid page",
                    self.client.get(
                        "/wiimc/search.plx", query_string={"q": "cats", "page": page}
                    ).text,
                )
        search.assert_not_called()

    def test_retry_deduplication(self):
        self.module.worker_started = True
        self.module.enqueue(self.vid)
        self.module.enqueue(self.vid)
        self.assertEqual(self.module.pending.qsize(), 1)
        self.module.patch_job(self.vid, status="error")
        self.module.enqueue(self.vid)
        self.assertEqual(self.module.pending.qsize(), 2)

    def test_checkbox_choice_is_persisted_and_validated(self):
        self.module.worker_started = True
        response = self.client.post("/api/prepare", json={"video": self.vid, "hardsubs": False})
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json["hardsubs"])
        saved = json.loads((Path(self.temp.name) / self.vid / "job.json").read_text())
        self.assertFalse(saved["hardsubs"])
        response = self.client.post("/api/prepare", json={"video": self.vid, "hardsubs": "false"})
        self.assertEqual(response.status_code, 400)

    def test_changing_captions_reprepares_only_finished_jobs(self):
        self.module.worker_started = True
        self.ready_file()
        job = self.module.enqueue(self.vid, hardsubs=False)
        self.assertEqual(job["status"], "queued")
        self.assertFalse(job["hardsubs"])
        with self.assertRaises(ValueError):
            self.module.enqueue(self.vid, hardsubs=True)
        self.assertEqual(self.module.pending.qsize(), 1)

    def test_restart_recovers_ready_and_marks_interrupted(self):
        self.ready_file()
        self.module.patch_job(self.vid)
        self.module.jobs.clear()
        self.module.load_jobs()
        self.assertEqual(self.module.jobs[self.vid]["status"], "ready")
        self.module.patch_job(self.vid, status="downloading")
        self.module.jobs.clear()
        self.module.load_jobs()
        self.assertEqual(self.module.jobs[self.vid]["status"], "error")

    def test_cross_site_and_public_requests_rejected(self):
        self.assertEqual(
            self.client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403
        )
        self.assertEqual(
            self.client.get("/", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code, 403
        )
        response = self.client.post(
            "/api/prepare", json={"video": self.vid}, headers={"Origin": "https://evil.test"}
        )
        self.assertEqual(response.status_code, 403)

    def test_remove_deletes_entire_video_folder_and_survives_restart(self):
        folder = self.ready_file().parent
        for name in ["video.mkv", "video.ts", "english.srt", "thumb.jpg", "source.part"]:
            (folder / name).write_bytes(b"test")
        (folder / "leftovers").mkdir()
        (folder / "leftovers" / "old-copy.mp4").write_bytes(b"old")
        self.module.patch_job(self.vid)
        self.module.metadata[self.vid] = {"title": "Test"}
        neighbor = Path(self.temp.name) / "roBkg-iPrbw"
        neighbor.mkdir()
        (neighbor / "video.mkv").write_bytes(b"keep")
        response = self.client.delete(f"/api/videos/{self.vid}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(folder.exists())
        self.assertNotIn(self.vid, self.module.jobs)
        self.assertNotIn(self.vid, self.module.metadata)
        self.assertEqual((neighbor / "video.mkv").read_bytes(), b"keep")
        self.assertEqual(self.client.get(f"/media/{self.vid}.mp4").status_code, 404)
        self.assertNotIn(self.vid, self.client.get("/wiimc/library.plx").text)
        self.module.load_jobs()
        self.assertNotIn(self.vid, self.module.jobs)
        self.module.worker_started = True
        self.assertEqual(self.module.enqueue(self.vid)["status"], "queued")

    def test_remove_rejects_active_jobs_and_cross_site_requests(self):
        path = self.ready_file()
        url = f"/api/videos/{self.vid}"
        self.assertEqual(
            self.client.delete(url, headers={"Origin": "https://evil.test"}).status_code, 403
        )
        for status in ["queued", "downloading", "converting"]:
            self.module.jobs[self.vid]["status"] = status
            self.assertEqual(self.client.delete(url).status_code, 409)
            self.assertTrue(path.exists())
        self.module.jobs[self.vid]["status"] = "error"
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.delete("/api/videos/not-an-id").status_code, 400)

    def test_remove_failure_keeps_job_for_retry(self):
        path = self.ready_file()
        with (
            patch.object(self.module.shutil, "rmtree", side_effect=PermissionError),
            self.assertLogs(level="ERROR"),
        ):
            self.assertEqual(self.client.delete(f"/api/videos/{self.vid}").status_code, 500)
        self.assertTrue(path.exists())
        self.assertIn(self.vid, self.module.jobs)

    def test_inflight_thumbnail_does_not_restore_deleted_folder(self):
        folder = self.ready_file().parent
        raw = io.BytesIO()
        self.module.Image.new("RGB", (20, 20), "red").save(raw, "JPEG")

        def download(*args, **kwargs):
            self.assertEqual(self.client.delete(f"/api/videos/{self.vid}").status_code, 200)
            return io.BytesIO(raw.getvalue())

        with patch.object(self.module, "urlopen", side_effect=download):
            response = self.client.get(f"/thumb/{self.vid}.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(folder.exists())

    def test_setup_and_wii_links_follow_installation_address(self):
        base = "http://10.20.30.40:4310"
        with patch.object(self.module, "BASE", base):
            page = self.client.get("/").text
            self.assertIn(f'{base}/wiimc/search.plx?q=', page)
            self.assertIn(f'{base}/wiimc/library.plx', page)
            self.assertNotIn("192.168.0.3", page)
            self.assertNotIn("a few little details", page)
            self.assertNotIn("Pause/play and volume use", page)
            root = ET.fromstring(self.client.get("/onlinemedia.xml").data)
            self.assertTrue(
                all(link.attrib["addr"].startswith(base + "/") for link in root.iter("link"))
            )
            self.assertIn(f"URL={base}/wiimc/library.plx", self.client.get("/wiimc/home.plx").text)


if __name__ == "__main__":
    unittest.main()
