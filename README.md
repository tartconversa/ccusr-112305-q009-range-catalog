# 跨款尺码资料

这里收录了测量尺度、共同试穿、发货规格和换货权益。记录为本地合成数据。每个 JSON 文件是来自同一来源的一组记录，字段含义与业务单位见 [资料口径](data/README.md)。

使用 Python 3.11 和标准库。检查资料概要与关系时，从项目根目录运行下面两条命令。

~~~sh
python3 scripts/inspect_sources.py
python3 -m unittest discover -s tests
~~~

查看单一数据来源时，可向检查脚本传入 JSON 文件名去掉扩展名后的名称。
