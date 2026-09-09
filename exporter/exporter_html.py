import copy
import html
import json
import math
import os
import shutil
import time
import hashlib

from wxManager.decrypt.decrypt_dat import batch_decode_image_multiprocessing
from wxManager.log import logger
from wxManager.model import MessageType, Me, AudioMessage
from exporter.exporter import ExporterBase, copy_files, decode_audios, get_new_filename, fetch_avatars
from jinja2 import Template
from utils.sourcefile_util import verify_source_file

icon_files = {
    'DOCX': ['doc', 'docx'],
    'XLS': ['xls', 'xlsx'],
    'CSV': ['csv'],
    'TXT': ['txt'],
    'ZIP': ['zip', '7z', 'rar'],
    'PPT': ['ppt', 'pptx'],
    'PDF': ['pdf'],
}


class HtmlExporter(ExporterBase):

    def export(self):
        messages = self.database.get_messages(self.contact.wxid, time_range=self.time_range)
        total_steps = len(messages)
        step = 1000
        start = 0
        while start < total_steps:
            self.export_piece(messages, start, step)
            start += step

    def export_piece(self, messages, start, step):
        print(f"【开始导出 HTML {self.contact.remark}_{str(start)}】")
        f_name = '.html'
        filename = os.path.join(self.origin_path, f'{self.contact.remark}_{str(start)}{f_name}')
        filename = get_new_filename(filename)
        # 获取当前脚本的目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 构建要读取的文件路径
        file_path = os.path.join(current_dir, 'resources', 'template.html')
        shutil.copytree(os.path.join(current_dir, 'resources', 'emoji'), os.path.join(self.origin_path, 'emoji'),dirs_exist_ok=True)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        template = Template(content)
        htmlfile_data = {
            'title_text': f"{self.contact.remark}_{str(start)}"
        }

        f = open(filename, 'w', encoding='utf-8')

        # avatar_urls, avatar_paths = self.get_avatar_urls()
        avatar_urls = []
        avatar_paths = []
        
        htmlfile_data['avatarPaths'] = json.dumps(avatar_paths)
        htmlfile_data['avatarUrls'] = json.dumps(avatar_urls)
        htmlfile_data['wxid'] = self.contact.wxid

        # QMe().save_avatar(self.origin_path + '/avatar/' + Me().wxid + '.png')
        # self.contact.save_avatar(self.origin_path + '/avatar/' + self.contact.wxid + '.png')
        date_id_map = {}
        timelineData = {}
        PageTimeline = {}
        server_id_Page = {}
        server_id_Idx = {}

        AllIndex = []
        ImageIndex = []
        FileIndex = []
        LinkIndex = []
        MusicIndex = []
        TransferIndex = []
        MiniProgramIndex = []
        VideoNumberIndex = []
        dateDataMap = {}
        i = 0
        itemsPerPage = 100
        num = 1
        html_json = []
        image_tasks = []
        video_tasks = []
        file_tasks = []
        audio_tasks = []
        avatar_tasks = {}  # key: str, value: Tuple[str, str]
        image_dir = os.path.join(self.origin_path, 'image')
        video_dir = os.path.join(self.origin_path, 'video')
        audio_dir = os.path.join(self.origin_path, 'voice')
        file_dir = os.path.join(self.origin_path, 'file')
        avatar_relative_dir = 'avatar'
        avatar_dir = os.path.join(self.origin_path, avatar_relative_dir)
        total_steps = len(messages)
        select_msg_cnt = 0  # 要导出的消息数量
        msg_index = 0

        def build_merged_html(merged_message):
            # Read the merged message template
            current_dir = os.path.dirname(os.path.abspath(__file__))
            template_path = os.path.join(current_dir, 'resources', 'mergeMsg.html')
            with open(template_path, "r", encoding="utf-8") as template_file:
                template_content = template_file.read()

            template = Template(template_content)

            # Prepare messages for template
            message_records = []
            dir_name, merged_msg_dir, relative_path = build_merged_msg_dirname(merged_message)
            for message in merged_message.messages:
                message_record = {
                    "avatar_src": message.avatar_src,
                    "avatar_file_name": message.avatar_file_name,
                    "display_name": message.display_name,
                    "type": message.type,
                    "str_time": message.str_time,
                    "files_dir": relative_path
                }

                match message.type:
                    case 1 | 2:
                        message_record["content"] = message.content
                    case 3:
                        message_record["content"] = message.file_name
                    case 43:
                        message_record["content"] = message.file_name
                    case 25769803825:
                        message_record["content"] = message.file_name
                        message_record["path"] = message.path
                    case 81604378673:
                        message_record["title"] = message.title
                        message_record["description"] = message.description
                        message_record["link_url"] = build_merged_msg_dirname(message)[0] + '.html'
                    case _:
                        message_record["content"] = "tmp"
                message_records.append(message_record)

            # Render the template with message data
            rendered_html = template.render(messages=message_records)
            return rendered_html

            
        def build_merged_msg_dirname(merged_message):
            # Format timestamp for directory and filename
            formatted_time = merged_message.str_time.replace('-', '').replace(' ', '-').replace(':', '')
            # Only use the last 6 digits of server_id
            # Convert server_id to string and get last 6 digits, or use empty string if None
            shortened_server_id = str(merged_message.server_id)[-6:] if merged_message.server_id else str(merged_message.timestamp)
            dir_name = formatted_time + '-' + shortened_server_id
            relative_path = dir_name + '.files'
            return dir_name, os.path.join(self.origin_path, relative_path), relative_path


        def create_merged_file(merged_message):
            dir_name, merged_msg_dir, relative_path = build_merged_msg_dirname(merged_message)
            os.makedirs(merged_msg_dir, exist_ok=True)

            # Generate HTML file with the same name as the directory
            html_filename = f"{dir_name}.html"
            html_file_path = os.path.join(self.origin_path, html_filename)

            # Create the HTML file for the merged messages
            html_content = build_merged_html(merged_message)
            # Write merged messages to the HTML file
            with open(html_file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

        def set_merged_media_filename(msg):
            msg.set_file_name()
            # 合并消息的 server_id 均为 0，同一秒的多个附件不能共用文件名。
            identity = msg.source_server_id or msg.md5 or msg.path
            if identity:
                stem, extension = os.path.splitext(msg.file_name)
                suffix = hashlib.md5(str(identity).encode('utf-8')).hexdigest()
                msg.file_name = f'{stem}_{suffix}{extension}'

        def parser_merged(merged_message):
            dir_name, merged_msg_dir, relative_path = build_merged_msg_dirname(merged_message)

            for msg in merged_message.messages:
                process_avatar(msg, avatar_tasks, avatar_dir)
                type_ = msg.type
                if type_ == MessageType.Image:
                    set_merged_media_filename(msg)
                    origin_file_path = os.path.join(Me().wx_dir, msg.path) if msg.path else ''
                    full_path = verify_source_file(origin_file_path)
                    if full_path == '':
                        print(f'合并消息{merged_message.str_time}中{msg.str_time} Image 源文件 {origin_file_path or "[未解析到附件路径]"} 不存在')
                    else:
                        image_tasks.append(
                            (
                                full_path,
                                merged_msg_dir,
                                msg.file_name
                            )
                        )
                    msg.path = f"./{relative_path}/{msg.file_name}"
                elif type_ == MessageType.File:
                    origin_file_path = os.path.join(Me().wx_dir, msg.path) if msg.path else ''
                    full_path = verify_source_file(origin_file_path)
                    if full_path == '':
                        print(f'合并消息{merged_message.str_time}中{msg.str_time} File 源文件 {origin_file_path or "[未解析到附件路径]"} 不存在')
                    else:
                        file_tasks.append(
                            (
                                full_path,
                                merged_msg_dir,
                                ''
                            )
                        )
                        msg.file_name = os.path.basename(full_path)
                        msg.path = f'./{relative_path}/{msg.file_name}'
                elif type_ == MessageType.Video:
                    set_merged_media_filename(msg)
                    origin_file_path = os.path.join(Me().wx_dir, msg.path) if msg.path else ''
                    full_path = verify_source_file(origin_file_path)
                    if full_path == '':
                        print(f'合并消息{merged_message.str_time}中{msg.str_time} Video 源文件 {origin_file_path or "[未解析到附件路径]"} 不存在')
                    else:
                        video_tasks.append(
                            (
                                full_path,
                                merged_msg_dir,
                                msg.file_name
                            )
                        )
                    msg.path = f'./{relative_path}/{msg.file_name}'
                elif type_ == MessageType.Audio:
                    if isinstance(msg, AudioMessage):
                        msg.set_file_name()
                        audio_tasks.append(
                            (
                                self.database.get_media_buffer(msg.server_id, self.contact.is_public()),
                                self.origin_path,
                                msg.file_name
                            )
                        )
                        msg.path = f'./{relative_path}/{msg.file_name + ".mp3"}'
                elif type_ == MessageType.MergedMessages:
                    parser_merged(msg)

            create_merged_file(merged_message)

        def process_avatar(message, avatar_tasks, avatar_dir):
            if hasattr(message, 'avatar_src') and message.avatar_src:
                avatar_md5 = hashlib.md5(message.avatar_src.encode()).hexdigest()
                avatar_filename = f"{avatar_md5}.jfif"
                avatar_filepath = os.path.join(avatar_dir, avatar_filename)
                avatar_tasks[avatar_md5] = (message.avatar_src, avatar_filepath)
                message.avatar_file_name = f'./{avatar_relative_dir}/{avatar_filename}'

        for index in range(start, min(start + step, len(messages))):
            message = messages[index]
            if not self._is_running:
                break
            if index and index % 1000 == 0:
                self.update_progress_callback(index / total_steps)
            type_ = message.type
            if not self.is_selected(message):
                continue
            server_id = message.server_id

            # Extract avatar_src from message, add to avatar_tasks
            process_avatar(message, avatar_tasks, avatar_dir)

            if type_ == MessageType.Image:
                ImageIndex.append(msg_index)
                message.set_file_name()
                origin_file_path = os.path.join(Me().wx_dir, message.path)
                full_path = verify_source_file(origin_file_path)
                if full_path == '':
                    print(f'消息{message.str_time}中 Image 源文件 {origin_file_path} 不存在')
                else:
                    image_tasks.append(
                        (
                            full_path,
                            self.origin_path,
                            message.file_name
                        )
                    )
                message.path = f"./{message.file_name}"
            elif type_ == MessageType.File:
                FileIndex.append(msg_index)
                origin_file_path = os.path.join(Me().wx_dir, message.path)
                full_path = verify_source_file(origin_file_path)
                if full_path == '':
                    print(f'消息{message.str_time}中 File 源文件 {origin_file_path} 不存在')
                else:
                    file_tasks.append(
                        (
                            full_path,
                            self.origin_path,
                            ''
                        )
                    )
                if os.path.isfile(origin_file_path):
                    message.path = f'./{os.path.basename(origin_file_path)}'
            elif type_ == MessageType.Video:
                ImageIndex.append(msg_index)
                message.set_file_name()
                origin_file_path = os.path.join(Me().wx_dir, message.path)
                full_path = verify_source_file(origin_file_path)
                if full_path == '':
                    print(f'消息{message.str_time}中 Video 源文件 {origin_file_path} 不存在')
                else:
                    video_tasks.append(
                        (
                            full_path,
                            self.origin_path,
                            message.file_name
                        )
                    )
                message.path = f'./{message.file_name}'
            elif type_ == MessageType.Audio:
                message.set_file_name()
                audio_tasks.append(
                    (
                        self.database.get_media_buffer(message.server_id, self.contact.is_public()),
                        self.origin_path,
                        message.file_name
                    )
                )
                message.path = f'./{message.file_name + ".mp3"}'
            elif type_ == MessageType.LinkMessage or type_ == MessageType.LinkMessage2 or type_ == MessageType.LinkMessage4 or type_ == MessageType.LinkMessage5 or type_ == MessageType.LinkMessage6:
                LinkIndex.append(msg_index)
            elif type_ == MessageType.Music:
                MusicIndex.append(msg_index)
            elif type_ == MessageType.Transfer:
                TransferIndex.append(msg_index)
            elif type_ == MessageType.Applet or type_ == MessageType.Applet2:
                MiniProgramIndex.append(msg_index)
            elif type_ == MessageType.WeChatVideo:
                VideoNumberIndex.append(msg_index)
            elif type_ == MessageType.MergedMessages:
                parser_merged(message)
            msg_index += 1
            is_select = True
            html_json.append(message.to_json())
            if is_select:
                select_msg_cnt += 1
                # 把时间戳转换为格式化时间
                str_time = message.str_time
                # 2024-01-01
                year = str_time[:4]
                month = int(str_time[5:7])
                curpage = math.ceil(select_msg_cnt / itemsPerPage)
                if str_time[:10] not in date_id_map:
                    date_id_map[str_time[:10]] = str(server_id)
                if str_time[:10] not in dateDataMap:
                    dateDataMap[str_time[:10]] = [curpage, str(server_id)]

                if year not in timelineData:
                    timelineData[year] = {}
                if month not in timelineData[year]:
                    timelineData[year][month] = []
                    timelineData[year][month].append(curpage)
                    timelineData[year][month].append(str(server_id))

                if curpage not in PageTimeline:
                    PageTimeline[curpage] = {}
                    PageTimeline[curpage]['year'] = year
                    PageTimeline[curpage]['month'] = month

                server_id_Page[str(server_id)] = curpage
                server_id_Idx[str(server_id)] = select_msg_cnt - 1

        logger.info('解析图片')
        # 使用多进程，导出所有图片
        batch_decode_image_multiprocessing(Me().xor_key, image_tasks)
        print('开始复制文件')
        logger.info(f'开始复制{len(video_tasks + file_tasks)}')
        # 使用多线程，复制文件、视频到导出文件夹
        copy_files(video_tasks + file_tasks)
        print('开始导出语音')
        logger.info('开始导出语音')
        decode_audios(audio_tasks)
        logger.info('开始导出头像')
        fetch_avatars(avatar_tasks)

        AllIndex = list(range(len(html_json)))

        replace_map = {
            "timelineData": timelineData,
            "PageTimeline": PageTimeline,
            "server_id_Page": server_id_Page,
            "server_id_Idx": server_id_Idx,
            "dateDataMap": dateDataMap,
            "AllIndex": AllIndex,
            "ImageIndex": ImageIndex,
            "FileIndex": FileIndex,
            "LinkIndex": LinkIndex,
            "MusicIndex": MusicIndex,
            "TransferIndex": TransferIndex,
            "MiniProgramIndex": MiniProgramIndex,
            "VideoNumberIndex": VideoNumberIndex
        }
        htmlfile_data.update(replace_map)



        def dict_to_js(dic: dict):
            for key, value in dic.items():
                if isinstance(value, str):
                    if value.startswith('http'):
                        dic[key] = value
                    else:
                        dic[key] = html.escape(value)
                elif isinstance(value, dict):
                    dic[key] = dict_to_js(value)
            return dic

        print('开始字符串转义')
        logger.info('开始字符串转义')
        # 字符串转义，防止JS出现语法错误
        html_data = []
        for item in copy.deepcopy(html_json):
            html_data.append(dict_to_js(item))

        htmlfile_data['chatMessages'] = json.dumps(html_data, ensure_ascii=False, indent=4)

        html_content = template.render(file_data=htmlfile_data)

        f.write(html_content)
        f.close()

        with open(filename + '.json', 'w', encoding='utf-8') as f:
            json.dump(html_json, f, ensure_ascii=False, indent=4)

        self.update_progress_callback(1)
        print(f"【完成导出 HTML {self.contact.remark}】{len(messages)}")
        self.finish_callback(self.exporter_id)
