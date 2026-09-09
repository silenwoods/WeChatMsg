"""Run with: python -m unittest discover -s checks -p '*_test.py' -v"""
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import xmltodict

from exporter.exporter_html import HtmlExporter
from utils.sourcefile_util import verify_source_file
from wxManager import Me, MessageType
from wxManager.db_v3.hard_link_video import HardLinkVideo
from wxManager.parser.util.protocbuf.msg_pb2 import MessageBytesExtra
from wxManager.parser.wechat_v3 import MergedMessageFactory


class MergedMediaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_wx_dir = Me().wx_dir
        Me().wx_dir = str(self.root)
        self.addCleanup(setattr, Me(), 'wx_dir', self.old_wx_dir)
        self.video_db = HardLinkVideo('unused.db')
        self.video_db.DB = sqlite3.connect(':memory:')
        self.video_db.open_flag = True
        self.video_db.DB.executescript('''
            CREATE TABLE HardLinkVideoID (DirID INTEGER, Dir TEXT);
            CREATE TABLE HardLinkVideoAttribute
                (Md5Hash INTEGER, MD5 BLOB, FileName TEXT, DirID2 INTEGER);
        ''')
        self.addCleanup(self.video_db.close)
        self.manager = SimpleNamespace(
            msg_db=SimpleNamespace(get_message_by_server_id=Mock(return_value=None)),
            get_video=self.video_db.get_video,
            get_image=Mock(return_value=''),
            get_file=Mock(return_value=''),
        )

    def write_media(self, path, content=b'media'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target.relative_to(self.root))

    def item(self, datatype='4', **values):
        return {'@datatype': datatype, 'srcMsgCreateTime': '1710000000',
                'sourcetime': '2024-03-10 00:00:00', 'sourcename': 'Sender',
                'fromnewmsgid': '12345', **values}

    def create(self, items):
        factory = MergedMessageFactory()
        xml = xmltodict.unparse({'msg': {'appmsg': {
            'title': 'Forwarded media', 'recorditem': {
                'recordinfo': {'datalist': {'dataitem': items}}}}}})
        factory.contacts['sender'] = SimpleNamespace(remark='Sender', small_head_img_url='')
        row = (1, 0, 49, 19, 0, 1710000000, 0, '', '2024-03-10 00:00:00', 67890)
        with patch.object(factory, 'common_attribute', return_value=(False, 'sender', xml)):
            return factory.create(row, 'group@chatroom', self.manager)

    def source(self, type_=43, content='<msg><videomsg length="0" /></msg>', path='', subtype=0):
        extra = MessageBytesExtra()
        if path:
            entry = extra.message2.add()
            entry.field1 = 4
            entry.field2 = 'account\\' + path.replace('/', '\\')
        return (1, 0, type_, subtype, 0, 1710000000, 0, content,
                '2024-03-10 00:00:00', 12345, extra.SerializeToString(), None, '')

    def add_video_index(self, md5, name):
        self.video_db.DB.execute('INSERT INTO HardLinkVideoID VALUES (1, ?)', ('2024-03',))
        self.video_db.DB.execute('INSERT INTO HardLinkVideoAttribute VALUES (0, ?, ?, 1)',
                                 (bytes.fromhex(md5), name))

    def test_video_without_fullmd5_uses_original_bytes_extra(self):
        path = self.write_media('FileStorage/Video/2024-03/original.mp4')
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(path=path)
        msg = self.create([self.item()]).messages[0]
        self.assertEqual(msg.path, path)
        self.assertEqual(msg.source_server_id, '12345')
        self.assertEqual(msg.server_id, 0)
        self.manager.msg_db.get_message_by_server_id.assert_called_once_with('', 12345)

    def test_video_without_fullmd5_uses_original_md5_index(self):
        md5 = 'ab' * 16
        self.add_video_index(md5, 'indexed.mp4')
        path = self.write_media('FileStorage/Video/2024-03/indexed.mp4')
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(
            content=f'<msg><videomsg md5="{md5}" /></msg>')
        self.assertEqual(self.create([self.item()]).messages[0].path, path)

    def test_old_fullmd5_and_download_in_another_month(self):
        md5 = 'cd' * 16
        self.add_video_index(md5, 'downloaded.mp4')
        path = self.write_media('FileStorage/Video/2024-04/downloaded.mp4')
        self.assertEqual(self.create([self.item(fullmd5=md5)]).messages[0].path, path)
        self.manager.msg_db.get_message_by_server_id.assert_not_called()

    def test_explicit_source_path_is_preserved(self):
        path = self.write_media('FileStorage/Video/2024-03/explicit.mp4')
        self.assertEqual(self.create([self.item(datasourcepath=path)]).messages[0].path, path)
        self.manager.msg_db.get_message_by_server_id.assert_not_called()

    def test_invalid_or_missing_original_is_not_misidentified(self):
        for source_id in ('', 'invalid', '0', str(2 ** 64), '999'):
            with self.subTest(source_id=source_id):
                self.assertEqual(self.create([self.item(fromnewmsgid=source_id)]).messages[0].path, '')
        self.manager.msg_db.get_message_by_server_id.assert_called_once_with('', 999)

    def test_original_of_different_type_is_rejected(self):
        path = self.write_media('FileStorage/File/document.txt')
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(type_=49, path=path, subtype=6)
        self.assertEqual(self.create([self.item()]).messages[0].path, '')

    def test_image_recovers_original_when_forwarded_thumbnail_is_missing(self):
        path = self.write_media('FileStorage/MsgAttach/original.dat')
        self.manager.get_image.side_effect = lambda content, *a, **k: path if content else ''
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(type_=3, content='<msg />')
        self.assertEqual(self.create([self.item('2', fullmd5='ef' * 16)]).messages[0].path, path)

    def test_file_recovers_original_bytes_extra(self):
        path = self.write_media('FileStorage/File/2024-03/document.txt')
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(
            type_=49, subtype=6, content='<msg><appmsg><title>document.txt</title>'
            '<appattach><fileext>txt</fileext></appattach></appmsg></msg>', path=path)
        self.assertEqual(self.create([self.item('8', datatitle='document.txt')]).messages[0].path, path)

    def test_nested_forwarded_video_is_resolved(self):
        path = self.write_media('FileStorage/Video/2024-03/nested.mp4')
        self.manager.msg_db.get_message_by_server_id.return_value = self.source(path=path)
        nested = self.item('17', recordxml={'recordinfo': {'datalist': {'dataitem': self.item()}}})
        self.assertEqual(self.create([nested]).messages[0].messages[0].path, path)

    def test_empty_paths_and_directories_do_not_trigger_a_disk_scan(self):
        with patch('utils.sourcefile_util.os.walk') as walk:
            for path in ('', str(self.root), str(self.root) + os.sep):
                self.assertEqual(verify_source_file(path), '')
            walk.assert_not_called()

    def test_html_copies_recovered_media_and_links_nested_messages(self):
        video = self.write_media('FileStorage/Video/2024-03/sample.mp4', b'video contents')
        second_video = self.write_media('FileStorage/Video/2024-03/second.mp4', b'another video')
        document = self.write_media('FileStorage/File/a document.txt', b'document contents')
        merged = self.create([
            self.item(datasourcepath=video),
            self.item('8', datatitle='a document.txt', datasourcepath=document),
            self.item('17', recordxml={'recordinfo': {'datalist': {'dataitem': self.item(datasourcepath=video)}}}),
            self.item(datasourcepath=second_video, fromnewmsgid='54321'),
        ])
        contact = SimpleNamespace(wxid='group@chatroom', remark='Group', is_chatroom=lambda: True)
        exporter = HtmlExporter(self.manager, contact, str(self.root / 'export'))
        with patch('exporter.exporter_html.fetch_avatars'), contextlib.redirect_stdout(io.StringIO()):
            exporter.export_piece([merged], 0, 1000)
        output = Path(exporter.origin_path)
        for msg in (merged.messages[0], merged.messages[2].messages[0]):
            self.assertEqual((output / msg.path).read_bytes(), b'video contents')
        self.assertNotEqual(merged.messages[0].path, merged.messages[3].path)
        self.assertEqual((output / merged.messages[3].path).read_bytes(), b'another video')
        file_message = merged.messages[1]
        self.assertEqual((output / file_message.path).read_bytes(), b'document contents')
        parent = output / '20240310-000000-67890.html'
        child = '20240310-000000-1710000000.html'
        content = parent.read_text(encoding='utf-8')
        self.assertIn(f'href="{child}"', content)
        self.assertTrue((output / child).is_file())
        self.assertIn(f'href="{file_message.path}"', content)


if __name__ == '__main__':
    unittest.main()
