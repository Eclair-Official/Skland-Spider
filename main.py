from SklandSpider import SklandSpider

def main():
    # 获取Scrapy项目的设置
    config = {
        "user_id_list": [
            ""
        ],
        "base_path": "data"
    }

    ss = SklandSpider(config)
    ss.start()


if __name__ == "__main__":
    main()
