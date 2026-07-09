"""
JSON export pipeline — writes items to a local JSONL file for backup.
"""

import json
import os


class JsonExportPipeline:
    """Write items to a local JSON file for testing/backup."""

    def open_spider(self, spider):
        os.makedirs("output", exist_ok=True)
        self.file = open("output/jobs.jsonl", "w", encoding="utf-8")
        self.count = 0

    def close_spider(self, spider):
        self.file.close()
        spider.logger.info(f"Exported {self.count} jobs to output/jobs.jsonl")

    def process_item(self, item, spider):
        row = dict(item)
        self.file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.count += 1
        return item
