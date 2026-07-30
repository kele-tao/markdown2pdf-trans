# 使用官方 Python 3.12 基础镜像
FROM python:3.12.2

# 替换为清华 Debian APT 源
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources

# pip 清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 系统依赖：pandoc + WeasyPrint GTK 运行时 + 中文字体
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pandoc \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libfontconfig1 \
        fonts-noto-cjk && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 拷贝项目代码
WORKDIR /app
COPY . /app

ENV PYTHONIOENCODING=UTF-8

EXPOSE 5000

# 启动：注意用 -m 让 src 当包
CMD ["python", "-m", "src.app"]
