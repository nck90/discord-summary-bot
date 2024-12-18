# bot/main.py

import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from bot.models import Base
import logging
import sys
import asyncio

# 환경 변수 로드
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
SENTRY_DSN = os.getenv('SENTRY_DSN')  # 선택 사항

# 로깅 설정
logger = logging.getLogger('discord_summary_bot')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# (선택 사항) Sentry 통합
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_logging = LoggingIntegration(
        level=logging.INFO,        # Breadcrumbs 로깅 레벨
        event_level=logging.ERROR  # 이벤트 로깅 레벨
    )

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[sentry_logging],
        traces_sample_rate=1.0
    )
    logger.info("Sentry 초기화 완료.")

# Intents 설정
intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용 인텐트 활성화
intents.guilds = True
intents.members = True        # 서버 멤버 인텐트 활성화 (필요 시)

# 봇 초기화 (명령어 프리픽스는 슬래시 명령어이므로 필요 없음)
bot = commands.Bot(command_prefix='!', intents=intents, logger=logger)

# SQLAlchemy Async Engine 및 Session 생성
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)

# Cog 임포트
from bot.cogs.summary import Summary

# Cog 로딩
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info('------')
    try:
        await bot.add_cog(Summary(bot, async_session))
        logger.info('Summary Cog loaded successfully.')
    except Exception as e:
        logger.error(f'Failed to load Summary Cog: {e}')

    # 봇 상태 설정
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="대화를 요약해요"))
    logger.info('Bot status set successfully.')

    # 슬래시 명령어 동기화
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} command(s).')
    except Exception as e:
        logger.error(f'Failed to sync commands: {e}')

# 봇 실행 전 초기화
async def main():
    # 필요한 경우 데이터베이스 초기화
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("데이터베이스 연결 및 초기화 완료.")
    except Exception as e:
        logger.error(f"데이터베이스 초기화 오류: {e}")
        return

    # 봇 시작
    try:
        await bot.start(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"봇 시작 오류: {e}")

# 실행
if __name__ == '__main__':
    asyncio.run(main())
