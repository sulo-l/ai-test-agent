#! /usr/bin/python3
# coding=utf-8
# @Time: 2026/1/15 14:41
# @Author: sulo
import uuid
from services import db

def main():
    session_id = str(uuid.uuid4())

    print("[CREATE SESSION]", session_id)

    # 1. 插入 session
    db.create_session(
        session_id=session_id,
        file_name="test.pdf",
        file_path="/tmp/test.pdf"
    )

    # 2. 更新状态
    db.update_session_status(session_id, "PARSED")

    # 3. 插入 session_data
    db.insert_session_data(
        session_id=session_id,
        data_type="pdf_text",
        content={
            "confirmed": "这是测试文本",
            "ocr": "这是 OCR 文本"
        }
    )

    print("数据写入完成")

if __name__ == "__main__":
    main()
