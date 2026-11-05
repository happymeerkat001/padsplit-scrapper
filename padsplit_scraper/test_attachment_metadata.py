"""Attachment names are display metadata, never an authorization or storage path."""
import unittest
from padsplit_scraper.attachment_metadata import safe_attachment_filename


class FilenameBoundaryTests(unittest.TestCase):
    def test_safe_names_keep_literal_and_benign_encoded_percent(self):
        for value in ('photo.jpg', '100% complete.jpg', 'photo%20one.jpg', 'résumé.pdf',
                      'password-reset.pdf', 'session-notes.txt', 'www-scene.jpg'):
            with self.subTest(value=value):self.assertTrue(safe_attachment_filename(value))

    def test_paths_urls_tokens_controls_and_deep_encodings_fail_closed(self):
        for value in ('../photo.jpg', 'folder%2Fphoto.jpg', 'folder\uff0fphoto.jpg',
                      'https%253A%252F%252Fprivate.invalid%252Ffile',
                      'token%3Dfictional-secret', 'file%3Fsignature%3Dfictional',
                      'www%2Eexample%2Einvalid', 'www%252Eexample%252Einvalid',
                      'password%3Dfictional', 'session%253Dfictional',
                      'client_secret%3Dfictional', 'X-Goog-Signature%3Dfictional',
                      'photo\u202ejpg', '%E0%A4%A', 'x'*501, None):
            with self.subTest(value_type=type(value).__name__):self.assertFalse(safe_attachment_filename(value))
        deeply_encoded='file%2525252525252Fsecret'
        self.assertFalse(safe_attachment_filename(deeply_encoded))

if __name__=='__main__':unittest.main()
