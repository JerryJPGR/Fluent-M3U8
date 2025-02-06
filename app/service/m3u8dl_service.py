# coding:utf-8
import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import List

from PySide6.QtCore import Qt, Signal, QProcess, QObject, QDateTime
import m3u8

from ..common.logger import Logger
from ..common.database.entity import Task
from ..common.config import cfg
from ..common.signal_bus import signalBus
from ..common.exception_handler import exceptionTracebackHandler


class M3U8DLCommand(Enum):
    """ M3U8DL command options """

    SAVE_DIR = "--save-dir"
    SAVE_NAME = "--save-name"
    THREAD_COUNT = "--thread-count"
    DOWNLOAD_RETRY_COUNT = "--download-retry-count"
    HTTP_REQUEST_TIMEOUT = "--http-request-timeout"
    HEADER = "--header"
    BINARY_MERGE = "--binary-merge"
    DEL_AFTER_DONE = "--del-after-done"
    APPEND_URL_PARAMS = "--append-url-params"
    MAX_SPEED = "--max-speed"
    SUB_FORMAT = "--sub-format"
    SELECT_VIDEO = "--select-video"
    SELECT_AUDIO = "--select-audio"
    SELECT_SUBTITLE = "--select-subtitle"
    AUTO_SELECT = "--auto-select"
    NO_DATE_INFO = "--no-date-info"
    CONCURRENT_DOWNLOAD = "--concurrent-download"
    USE_SYSTEM_PROXY = "--use-system-proxy"
    CUSTOM_PROXY = "--custom-proxy"

    def command(self, value=None):
        if value is None:
            return self.value

        if isinstance(value, list):
            return f"{self.value}={','.join(value)}"

        value = str(value)
        return f'{self.value}="{value}"' if value.find(" ") >= 0 else f'{self.value}={value}'


@dataclass
class DownloadProgressInfo:
    """ Download progress information """

    currentChunk: int = 0
    totalChunks: int = 0
    speed: str = ""
    remainTime: str = ""
    currentSize: str = ""
    totalSize: str = ""


class M3U8DLCommandLineParser(QObject):
    """ M3U8DL Command line parser """

    def __init__(self):
        super().__init__()
        self._parser = argparse.ArgumentParser(
            description="handle N_m3u8DL-RE's command line")
        self._setUpParser()

    def _setUpParser(self):
        self._parser.add_argument('url', type=str, nargs='?', default=None)
        self._parser.add_argument(M3U8DLCommand.SAVE_NAME.value, type=str)
        self._parser.add_argument(M3U8DLCommand.SAVE_DIR.value, type=str)

    def parse(self, options: List[str]) -> Task:
        """ process args """
        args, _ = self._parser.parse_known_args(options)
        task = Task(
            fileName=args.save_name,
            saveFolder=args.save_dir,
            command=" ".join(options),
        )
        return task


class M3U8DLService(QObject):

    downloadCreated = Signal(Task)
    downloadProcessChanged = Signal(int, DownloadProgressInfo)   # pid, info
    downloadFinished = Signal(int, bool, str)   # pid, isSuccess, message

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.logger = Logger("download")
        self.cmdParser = M3U8DLCommandLineParser()

    @exceptionTracebackHandler("download", False)
    def download(self, options: List[str]):
        options = self.generateCommand(options)
        task = self.cmdParser.parse([self.downloaderPath, *options])

        self.logger.info(f"添加下载任务：{self.downloaderPath} {' '.join(options)}")
        taskLogger = Logger("Tasks/" + task.createTime.toString(Qt.DateFormat.ISODateWithMs))

        process = QProcess()
        process.setWorkingDirectory(str(Path(self.downloaderPath).parent))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        process.readyRead.connect(lambda: self._onDownloadMessage(process, taskLogger))
        process.finished.connect(lambda code, status: self._onDownloadFinished(process, code, status))
        # compileTerminated.connect(process.terminate)
        process.start(self.downloaderPath, options)

        task.pid = process.processId()
        self.downloadCreated.emit(task)
        return True

    def _onDownloadMessage(self, process: QProcess, logger: Logger):
        message = process.readAllStandardOutput().toStdString()
        logger.info(message)

        # parse progress message
        regex = r"(\d+)\/(\d+)\s+(\d+\.\d+)%\s+(\d+\.\d+)(KB|MB|GB)\/(\d+\.\d+)(KB|MB|GB)\s+(\d+\.\d+)(GBps|MBps|KBps|Bps)\s(.+)"
        match = re.search(regex, message)

        if not match:
            return

        info = DownloadProgressInfo(
            currentChunk=int(match[1]),
            totalChunks=int(match[2]),
            currentSize=match[4]+match[5],
            totalSize=match[6]+match[7],
            speed=match[8]+match[9],
            remainTime=match[10]
        )
        self.downloadProcessChanged.emit(process.processId(), info)

    def _onDownloadFinished(self, process: QProcess, code, status: QProcess.ExitStatus):
        if status == QProcess.ExitStatus.NormalExit:
            self.downloadFinished.emit(process.processId(), True, "")
        else:
            self.downloadFinished.emit(process.processId(), False, process.errorString())

    def generateCommand(self, options):
        # options.extend([
        #     M3U8DLCommand.SELECT_AUDIO.command(),
        #     'for=best',
        #     M3U8DLCommand.SELECT_SUBTITLE.command(),
        #     'for=all'
        # ])
        return options

    @exceptionTracebackHandler("download", [])
    def getStreamInfos(self, url: str, timeout=10):
        """ Returns the available streams information """
        response = m3u8.load(url, timeout=timeout)

        if not response.playlists:
            return []

        streamInfos = []
        for playlist in response.playlists:
            streamInfos.append(playlist.stream_info)

        return streamInfos

    @property
    def downloaderPath(self):
        return cfg.get(cfg.m3u8dlPath)


m3u8Service = M3U8DLService()