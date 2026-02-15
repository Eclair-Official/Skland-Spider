# -*- coding: utf-8 -*-
"""
@Author ： Eclair
@Date ： 2025/11/22 13:01
@File ：SklandSpider.py
@IDE ：PyCharm
@Description ：
"""
import datetime
import gzip
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import brotli  # 导入 brotli 库用于解压
import m3u8
import requests
from pathvalidate import sanitize_filename

from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from seleniumwire import webdriver


class SklandSpider(object):
    def __init__(self, config):
        self.user_list = config["user_id_list"]
        self.base_url = "https://www.skland.com/profile?id="
        self.base_path = config["base_path"]
        # --- 配置区 ---
        # 1. 修改这里为你的 chromedriver.exe 的路径
        self.WEBDRIVER_PATH = 'chromedriver.exe'
        # 4. 我们要拦截的API的URL部分（用于模糊匹配）
        self.API_TARGET_URL_FRAGMENT = '/web/v1/user/items'
        # 5. 设置滚动后等待新请求的超时时间（秒）
        self.REQUEST_TIMEOUT = 15
        self.MAX_WORKERS = 8

    def init_driver(self):
        print("正在配置无头浏览器...")
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # 无头模式
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        # 禁用图片加载，可以提升抓取速度
        chrome_prefs = {"profile.managed_default_content_settings.images": 2}
        chrome_options.add_experimental_option("prefs", chrome_prefs)

        service = ChromeService(executable_path=self.WEBDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver

    def start(self):
        driver = self.init_driver()
        try:
            for USER_ID in self.user_list:
                TARGET_USER_PROFILE_URL = self.base_url + USER_ID
                print(f"正在后台访问页面: {TARGET_USER_PROFILE_URL}")
                driver.get(TARGET_USER_PROFILE_URL)
                print("等待页面初始加载...")
                time.sleep(5)  # 等待页面加载出第一屏内容

                all_items_data = self.scroll_and_intercept_data(driver, self.API_TARGET_URL_FRAGMENT)
                if all_items_data:
                    # 按发布时间倒序排列，确保数据的逻辑顺序
                    all_items_data_sorted = sorted(all_items_data, key=lambda x: x['item'].get('publishedAtTs', 0),
                                                   reverse=True)
                    user_info = all_items_data_sorted[0]['user']
                    user_path = self.init_base_path(f"{user_info['nickname']}_{user_info['id']}")
                    print(f"用户存储目录: {user_path}")
                    output_filename = os.path.join(user_path, f'skland_items_{USER_ID}_with_brotli.json')
                    with open(output_filename, 'w', encoding='utf-8') as f:
                        json.dump(all_items_data_sorted, f, ensure_ascii=False, indent=4)

                    for items in all_items_data_sorted:
                        self.process_and_download_for_item(items, user_path)

                    print(f"\n所有数据已成功保存到文件: {output_filename}")
                    print(f"总共拦截并获取到 {len(all_items_data_sorted)} 条 items。")
                else:
                    print("\n未能通过拦截获取到任何 items 数据。")

        finally:
            print("\n任务完成，正在关闭无头浏览器...")
            driver.quit()

    def scroll_and_intercept_data(self, driver, api_url_fragment):
        """
        滚动页面并拦截所有API请求，同时处理 Gzip 和 Brotli 压缩。
        """
        global response_body_bytes, content_encoding
        all_items = []
        captured_requests = set()

        print("开始滚动页面以触发数据加载...")

        last_height = driver.execute_script("return document.body.scrollHeight")
        last_request_time = time.time()

        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

            new_requests_found = False
            for request in driver.requests:
                if request.method == 'GET' and api_url_fragment in request.url and request.response:
                    if request.url not in captured_requests:
                        new_requests_found = True
                        last_request_time = time.time()
                        captured_requests.add(request.url)

                        try:
                            # 获取原始响应体（二进制格式）
                            response_body_bytes = request.response.body

                            # 检查 Content-Encoding 响应头
                            content_encoding = request.response.headers.get('Content-Encoding', '').lower()

                            decompressed_body = b''  # 初始化解压后的字节流
                            if content_encoding == 'gzip':
                                print(f"检测到Gzip压缩，正在解压... URL: {request.url}")
                                decompressed_body = gzip.decompress(response_body_bytes)
                            elif content_encoding == 'br':
                                print(f"检测到Brotli压缩，正在解压... URL: {request.url}")
                                decompressed_body = brotli.decompress(response_body_bytes)
                            else:
                                # 如果没有声明压缩，或者使用其他不支持的编码，直接使用原始body
                                decompressed_body = response_body_bytes

                            # 将解压后的字节流解码为UTF-8字符串
                            response_body_str = decompressed_body.decode('utf-8')

                            # 解析JSON
                            data = json.loads(response_body_str)

                            if data.get('code') == 0:
                                items_on_page = data.get('data', {}).get('list', [])
                                if items_on_page:
                                    print(
                                        f"成功获取 {len(items_on_page)} 条数据。总计: {len(all_items) + len(items_on_page)}")
                                    all_items.extend(items_on_page)
                            else:
                                print(f"API返回错误: {data.get('message', '未知错误')}。URL: {request.url}")

                        except (gzip.BadGzipFile, brotli.error) as e:
                            print(f"解压失败 ({content_encoding}): {e}。尝试直接解码... URL: {request.url}")
                            # 如果声明了压缩但解压失败，尝试直接解码，以防头信息错误
                            try:
                                response_body_str = response_body_bytes.decode('utf-8')
                                data = json.loads(response_body_str)
                                # ...后续处理逻辑...
                            except Exception as e_inner:
                                print(f"直接解码也失败，跳过此请求。错误: {e_inner}")
                                continue
                        except (UnicodeDecodeError, json.JSONDecodeError) as e:
                            print(f"解码或解析JSON时出错: {e}。URL: {request.url}")
                            print(f"原始响应体(前100字节): {response_body_bytes[:100]}")
                        except Exception as e:
                            print(f"处理拦截的请求时发生未知错误: {e}。URL: {request.url}")

            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                if not new_requests_found and (time.time() - last_request_time > self.REQUEST_TIMEOUT):
                    print(f"在 {self.REQUEST_TIMEOUT} 秒内未检测到新请求且页面高度不变，认为加载完成。")
                    break
            else:
                last_height = new_height

            time.sleep(1)

        return all_items

    def init_base_path(self, path):
        base_path = os.path.join(self.base_path, path)
        # 如果日志文件夹不存在，则创建
        if not os.path.isdir(self.base_path):
            os.makedirs(self.base_path)
        if not os.path.isdir(base_path):
            os.makedirs(base_path)
        return base_path

    def download_file(self, url, save_path, headers=None):

        """
          通用的文件下载函数，如果文件已存在且大小大于0，则跳过。
          """
        # 【新增】检查文件是否存在且不为空
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            print(f"    [跳过] {os.path.basename(save_path)} (文件已存在)")
            return True
        if headers is None:
            headers = {}
        try:
            response = requests.get(url, stream=True, headers=headers, timeout=30)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except Exception as e:
            print(f"    [失败] {os.path.basename(save_path)}: {e}")
            # 删除可能下载失败的不完整文件
            if os.path.exists(save_path):
                os.remove(save_path)
            return False

    def download_m3u8_video(self, m3u8_url, final_save_path, item_id):
        """
          使用 m3u8 库解析并下载视频流，如果最终视频已存在且不为空，则跳过。
          """
        # 【新增】检查最终合并的视频文件是否已存在
        if os.path.exists(final_save_path) and os.path.getsize(final_save_path) > 0:
            print(f"  [{item_id}] [跳过] 视频 {os.path.basename(final_save_path)} (文件已存在)")
            return True
        print(f"  [{item_id}] 开始处理视频: {m3u8_url}")
        temp_dir = os.path.join(os.path.dirname(final_save_path), 'temp_ts_files')
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. 解析 m3u8 播放列表
            headers = {'Referer': 'https://www.skland.com/'}
            m3u8_obj = m3u8.load(m3u8_url, headers=headers)

            if not m3u8_obj.segments:
                print(f"  [{item_id}] 警告: m3u8文件中未找到视频片段。")
                return False

            # 2. 准备所有 .ts 文件的下载任务
            download_tasks = []
            for i, segment in enumerate(m3u8_obj.segments):
                ts_url = segment.absolute_uri
                ts_filename = f"{i:04d}.ts"  # 使用0001.ts, 0002.ts...格式确保顺序
                ts_path = os.path.join(temp_dir, ts_filename)
                download_tasks.append((ts_url, ts_path))

            print(f"  [{item_id}] 准备下载 {len(download_tasks)} 个视频片段...")

            # 3. 使用线程池并发下载所有 .ts 文件
            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                futures = {executor.submit(self.download_file, url, path, headers): (url, path) for url, path in
                           download_tasks}
                for future in as_completed(futures):
                    url, path = futures[future]
                    if future.result():
                        print(f"    [成功] {os.path.basename(path)}")

            # 4. 检查所有片段是否都已下载成功
            downloaded_ts_files = sorted([f for f in os.listdir(temp_dir) if f.endswith('.ts')])
            if len(downloaded_ts_files) != len(download_tasks):
                print(f"  [{item_id}] 错误: 部分视频片段下载失败，放弃合并。")
                return False

            # 5. 合并所有 .ts 文件
            print(f"  [{item_id}] 开始合并视频片段到 {os.path.basename(final_save_path)}...")
            with open(final_save_path, 'wb') as outfile:
                for ts_filename in downloaded_ts_files:
                    ts_path = os.path.join(temp_dir, ts_filename)
                    with open(ts_path, 'rb') as infile:
                        outfile.write(infile.read())

            print(f"  [{item_id}] 视频合并成功！")
            return True

        except Exception as e:
            print(f"  [{item_id}] 处理视频时发生未知错误: {e}")
            return False

        finally:
            # 6. 清理临时文件
            if os.path.exists(temp_dir):
                for file in os.listdir(temp_dir):
                    os.remove(os.path.join(temp_dir, file))
                os.rmdir(temp_dir)
                # print(f"  [{item_id}] 已清理临时文件。")

    def process_and_download_for_item(self, item_data, base_download_dir):
        """
        为单个item解析并下载所有origin图片和最高清视频
        """
        item = item_data.get('item', {})
        item_id = item.get('id')
        title = sanitize_filename(item.get('title', f'untitled'))
        # 转换为本地时间（你的操作系统时区）
        local_time = datetime.datetime.fromtimestamp(item.get("timestamp"))
        item_dir = os.path.join(base_download_dir, f"{local_time.strftime('%Y-%m-%d %H-%M-%S')}_{title}_{item_id}")
        os.makedirs(item_dir, exist_ok=True)
        output_filename = os.path.join(item_dir, f'{title}_{item_id}_with_brotli.json')
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(item_data, f, ensure_ascii=False, indent=4)
        print(f"--- 开始处理 Item: [{item_id}] {title} ---")

        download_tasks = []

        # --- 准备图片下载任务 ---
        image_list = item.get('imageListSlice', [])
        for idx, img in enumerate(image_list):
            origin_info = next((d for d in img.get('displayInfos', []) if d.get('style') == 'origin'), None)
            if origin_info:
                url = origin_info.get('url')
                if url:
                    file_extension = os.path.splitext(url.split('?')[0])[-1]
                    save_path = os.path.join(item_dir, f'{title}_{idx + 1}_{file_extension}')
                    download_tasks.append(('image', url, save_path))

        # --- 准备视频下载任务 ---
        video_list = item.get('videoListSlice', [])

        for video_idx, video in enumerate(video_list):
            video_id_from_json = video.get('id')  # 尝试从JSON中获取ID
            resolutions = video.get('resolutions', [])

            if resolutions:
                resolutions.sort(key=lambda r: (int(r.get('height', 0)), int(r.get('width', 0))), reverse=True)
                highest_res_video = resolutions[0]
                video_url = highest_res_video.get('playURL')

                if video_url:
                    resolution_tag = sanitize_filename(highest_res_video.get("resolution", "high"))

                    # 使用索引来确保文件名唯一且简洁
                    video_filename = f'{title}_{video_idx + 1}_{resolution_tag}.mp4'
                    video_save_path = os.path.join(item_dir, video_filename)

                    if not (os.path.exists(video_save_path) and os.path.getsize(video_save_path) > 0):
                        # 创建一个更详细的log_id，用于在下载时区分是哪个视频
                        log_id = f'{item_id}_vid{video_idx + 1}'
                        download_tasks.append(('video', video_url, video_save_path, log_id))
        # --- 执行下载任务 ---
        with ThreadPoolExecutor(max_workers=3) as executor:  # 控制并发Item数
            futures = []
            for task in download_tasks:
                task_type = task[0]
                if task_type == 'image':
                    _, url, path = task
                    futures.append(
                        executor.submit(self.download_file, url, path, {'Referer': 'https://www.skland.com/'}))
                elif task_type == 'video':
                    _, url, path, item_id_arg = task
                    futures.append(executor.submit(self.download_m3u8_video, url, path, item_id_arg))

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"一个下载任务失败: {e}")


if __name__ == '__main__':
    spider = SklandSpider()
    spider.start()
