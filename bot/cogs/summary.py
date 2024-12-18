import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from bot.models import Summary as SummaryModel
import os
from enum import Enum

# 요약 수준을 정의하는 Enum
class SummaryLevel(Enum):
    SIMPLE = "간단"
    DETAILED = "상세"

# 시간대 옵션을 정의하는 Enum
class TimeRangeOption(Enum):
    LAST_HOUR = "지난 1시간"
    LAST_24_HOURS = "지난 24시간"
    TODAY = "오늘"
    YESTERDAY = "어제"
    CUSTOM = "사용자 정의"

class Summary(commands.Cog):
    def __init__(self, bot: commands.Bot, async_session):
        self.bot = bot
        self.async_session = async_session
        self.logger = logging.getLogger('discord_summary_bot.Summary')
        self.gemini_api_key = os.getenv('GEMINI_API_KEY')
        self.gemini_api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
        self.logger.info("Summary Cog initialized.")

    # 요약 수준 선택을 위한 View 클래스
    class SummaryLevelView(discord.ui.View):
        def __init__(self, logger, cog):
            super().__init__(timeout=60)  # 1분 후 타임아웃
            self.logger = logger
            self.cog = cog
            self.message = None

        @discord.ui.select(
            placeholder="요약 수준을 선택하세요.",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=SummaryLevel.SIMPLE.value, value=SummaryLevel.SIMPLE.value, description="간단한 요약"),
                discord.SelectOption(label=SummaryLevel.DETAILED.value, value=SummaryLevel.DETAILED.value, description="상세한 요약"),
            ]
        )
        async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
            selected_level = SummaryLevel(select.values[0])
            self.logger.info(f"요약 수준 선택: {selected_level.value}")

            # 다음 단계: 시간대 선택
            time_view = Summary.TimeRangeView(selected_level, self.logger, self.cog)
            await interaction.response.edit_message(content="🕒 요약할 시간대를 선택하세요.", view=time_view)

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                await self.message.edit(view=self)

    # 시간대 선택을 위한 View 클래스
    class TimeRangeView(discord.ui.View):
        def __init__(self, summary_level, logger, cog):
            super().__init__(timeout=60)  # 1분 후 타임아웃
            self.summary_level = summary_level
            self.logger = logger
            self.cog = cog

        @discord.ui.select(
            placeholder="시간대를 선택하세요.",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=TimeRangeOption.LAST_HOUR.value, value=TimeRangeOption.LAST_HOUR.value),
                discord.SelectOption(label=TimeRangeOption.LAST_24_HOURS.value, value=TimeRangeOption.LAST_24_HOURS.value),
                discord.SelectOption(label=TimeRangeOption.TODAY.value, value=TimeRangeOption.TODAY.value),
                discord.SelectOption(label=TimeRangeOption.YESTERDAY.value, value=TimeRangeOption.YESTERDAY.value),
                discord.SelectOption(label=TimeRangeOption.CUSTOM.value, value=TimeRangeOption.CUSTOM.value, description="사용자 정의 시간대"),
            ]
        )
        async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
            selected_range = TimeRangeOption(select.values[0])
            self.logger.info(f"시간대 선택: {selected_range.value}")

            if selected_range == TimeRangeOption.CUSTOM:
                # 사용자 정의 시간대 입력을 위한 모달 호출
                modal = Summary.CustomTimeRangeModal(self.summary_level, self.logger, self.handle_summary, self.cog)
                await interaction.response.send_modal(modal)
            else:
                # 미리 정의된 시간대 처리
                start_time, end_time = self.get_time_range(selected_range)
                await self.handle_summary(interaction, self.summary_level, start_time, end_time)

        def get_time_range(self, selected_range: TimeRangeOption):
            now = datetime.now(timezone.utc)
            if selected_range == TimeRangeOption.LAST_HOUR:
                start_time = now - timedelta(hours=1)
                end_time = now
            elif selected_range == TimeRangeOption.LAST_24_HOURS:
                start_time = now - timedelta(hours=24)
                end_time = now
            elif selected_range == TimeRangeOption.TODAY:
                start_time = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
                end_time = now
            elif selected_range == TimeRangeOption.YESTERDAY:
                start_time = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=1)
                end_time = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
            else:
                # 기본값
                start_time = now - timedelta(hours=2)
                end_time = now
            self.logger.debug(f"파싱된 시간대 - 시작: {start_time}, 종료: {end_time}")
            return start_time, end_time

        async def handle_summary(self, interaction: discord.Interaction, summary_level: SummaryLevel, start_time, end_time):
            """
            요약 생성 및 전송을 처리하는 메소드
            """
            self.logger.info(f"요약 생성 시작: 수준={summary_level.value}, 시간대=시작={start_time}, 종료={end_time}")

            # 메시지 수집
            messages = []
            channel = interaction.channel
            try:
                if isinstance(channel, discord.TextChannel):
                    async for message in channel.history(limit=None, after=start_time, before=end_time):
                        if not message.author.bot:
                            timestamp = message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                            messages.append(f"{timestamp} | {message.author.display_name}: {message.content}")
                    self.logger.info(f"수집된 메시지 수: {len(messages)}")
                elif isinstance(channel, discord.DMChannel):
                    async for message in channel.history(limit=None, after=start_time, before=end_time):
                        if not message.author.bot:
                            timestamp = message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                            messages.append(f"{timestamp} | {message.author.display_name}: {message.content}")
                    self.logger.info(f"DM 수집된 메시지 수: {len(messages)}")
                else:
                    await interaction.followup.send("❌ 이 채널에서는 요약 기능을 사용할 수 없습니다.", ephemeral=True)
                    return
            except discord.Forbidden:
                self.logger.warning("메시지 읽기 권한이 없습니다.")
                await interaction.followup.send("❌ 메시지 읽기 권한이 없습니다.", ephemeral=True)
                return
            except discord.HTTPException as e:
                self.logger.error(f"메시지 수집 중 HTTP 오류: {e}")
                await interaction.followup.send("❌ 메시지 수집 중 오류가 발생했습니다.", ephemeral=True)
                return

            if not messages:
                self.logger.info("해당 시간대에 메시지가 없습니다.")
                await interaction.followup.send("⚠️ 해당 시간대에 메시지가 없습니다.", ephemeral=True)
                return

            # 메시지 텍스트로 합치기
            conversation = "\n".join(messages)
            self.logger.debug(f"대화 내용: {conversation}")

            # 요약 생성 로직
            try:
                summary = await self.cog.process_summary(conversation, summary_level)
                self.logger.info("요약 생성 완료.")
                if not summary:
                    self.logger.warning("요약 내용이 비어있습니다.")
                    await interaction.followup.send("⚠️ 요약 내용이 비어있습니다.", ephemeral=True)
                    return
            except Exception as e:
                self.logger.error(f"요약 생성 중 오류: {e}")
                await interaction.followup.send(f"❌ 요약 생성 중 오류가 발생했습니다: {e}", ephemeral=True)
                return

            # 임베드 생성 및 페이지 나누기
            summary_pages = self.cog.split_text_into_pages(summary, max_length=2048)
            pages = []
            for page_content in summary_pages:
                embed = discord.Embed(
                    title="📋 대화 요약",
                    description=page_content,
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text=f"요약 요청자: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
                pages.append(embed)

            if len(pages) > 1:
                view = Summary.PaginationView(pages)
                message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
                view.message = message
            else:
                if isinstance(channel, discord.TextChannel):
                    try:
                        thread = await interaction.channel.create_thread(
                            name=f"요약-{interaction.user.display_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                            type=discord.ChannelType.private_thread,
                            invitable=False,
                            reason="Summary thread for user"
                        )
                        await thread.add_user(interaction.user)
                        close_view = Summary.CloseThreadView(thread, interaction.user.id, self.logger)
                        await thread.send(embed=pages[0], view=close_view)
                        self.logger.info(f"비공개 쓰레드 '{thread.name}'에 요약을 전송했습니다.")

                        thread_url = thread.jump_url
                        await interaction.followup.send(f"✅ 비공개 스레드가 생성되었습니다: {thread_url}", ephemeral=True)

                    except discord.Forbidden:
                        self.logger.error("비공개 쓰레드 생성 권한이 없습니다.")
                        await interaction.followup.send("❌ 비공개 쓰레드를 생성할 권한이 없습니다.", ephemeral=True)
                        return
                    except discord.HTTPException as e:
                        self.logger.error(f"비공개 쓰레드 생성 또는 메시지 전송 중 HTTP 오류: {e}")
                        await interaction.followup.send("❌ 비공개 쓰레드 생성 또는 메시지 전송 중 오류가 발생했습니다.", ephemeral=True)
                        return
                else:
                    try:
                        embed = pages[0]
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        self.logger.info("요약 임베드를 직접 전송했습니다.")
                    except discord.HTTPException as e:
                        self.logger.error(f"임베드 전송 중 HTTP 오류: {e}")
                        await interaction.followup.send("❌ 요약 임베드를 전송하는 중 오류가 발생했습니다.", ephemeral=True)
                        return

            # 데이터베이스에 요약 저장 (텍스트 채널인 경우만 저장)
            if isinstance(channel, discord.TextChannel):
                await self.cog.save_summary(interaction.guild.id, interaction.channel.id, interaction.user.id, start_time, end_time, summary)
            else:
                self.logger.info("비텍스트 채널에서 요약을 저장하지 않았습니다.")

    # 사용자 정의 시간대 입력을 위한 모달 클래스
    class CustomTimeRangeModal(discord.ui.Modal):
        def __init__(self, summary_level, logger, callback, cog):
            super().__init__(title="사용자 정의 시간대 입력")
            self.summary_level = summary_level
            self.logger = logger
            self.callback = callback
            self.cog = cog

            self.start = discord.ui.TextInput(
                label="시작 날짜 및 시간 (YYYY-MM-DD HH:MM)",
                placeholder="예: 2024-12-17 10:00",
                required=True
            )
            self.end = discord.ui.TextInput(
                label="종료 날짜 및 시간 (YYYY-MM-DD HH:MM)",
                placeholder="예: 2024-12-18 18:00",
                required=True
            )
            self.add_item(self.start)
            self.add_item(self.end)

        async def on_submit(self, interaction: discord.Interaction):
            start_str = self.start.value
            end_str = self.end.value
            self.logger.info(f"사용자 정의 시간대 입력: 시작={start_str}, 종료={end_str}")
            try:
                start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                end_time = datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                if end_time <= start_time:
                    raise ValueError("종료 시간은 시작 시간보다 늦어야 합니다.")
                self.logger.debug(f"파싱된 사용자 정의 시간대: 시작={start_time}, 종료={end_time}")
            except ValueError as e:
                self.logger.error(f"사용자 정의 시간대 파싱 오류: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ 시간대 입력 오류: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ 시간대 입력 오류: {e}", ephemeral=True)
                return

            await self.callback(interaction, self.summary_level, start_time, end_time)

    @app_commands.command(name="요약", description="대화를 요약합니다.")
    async def summarize(self, interaction: discord.Interaction):
        self.logger.info(f"/요약 명령어 실행: 사용자={interaction.user}")
        await interaction.response.defer(ephemeral=True)
        view = self.SummaryLevelView(self.logger, self)
        message = await interaction.followup.send("📜 요약 수준을 선택하세요.", view=view, ephemeral=True)
        view.message = message

    # 회의록 검색 명령어
    @app_commands.command(name="회의록검색", description="특정 날짜의 요약본을 검색합니다.")
    @app_commands.describe(date="검색할 날짜 (예: 2023-10-01)")
    async def search_summaries(self, interaction: discord.Interaction, date: str):
        self.logger.info(f"/회의록검색 명령어 실행: 사용자={interaction.user}, 날짜={date}")
        await interaction.response.defer(ephemeral=True)

        # 날짜 형식 검증
        try:
            search_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            next_day = search_date + timedelta(days=1)
            self.logger.info(f"검색 날짜: {search_date}, 다음 날: {next_day}")
        except ValueError:
            self.logger.error(f"날짜 형식 오류: {date}")
            await interaction.followup.send("❌ 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력해주세요.", ephemeral=True)
            return

        # 데이터베이스에서 요약본 검색
        try:
            async with self.async_session() as session:
                stmt = select(SummaryModel).where(
                    SummaryModel.user_id == str(interaction.user.id),
                    SummaryModel.created_at >= search_date,
                    SummaryModel.created_at < next_day
                )
                result = await session.execute(stmt)
                summaries = result.scalars().all()
            self.logger.info(f"검색된 요약본 수: {len(summaries)}")
        except SQLAlchemyError as e:
            self.logger.error(f"데이터베이스 조회 중 오류: {e}")
            await interaction.followup.send("❌ 데이터베이스 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        if not summaries:
            self.logger.info("해당 날짜에 생성된 요약본이 없습니다.")
            await interaction.followup.send("⚠️ 해당 날짜에 생성된 요약본이 없습니다.", ephemeral=True)
            return

        # 검색 결과 임베드 생성
        embed = discord.Embed(
            title=f"📄 {date} 요약본 검색 결과",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )

        for summary in summaries:
            embed.add_field(
                name=f"요약 ID: {summary.id}",
                value=f"채널: <#{summary.channel_id}>\n생성 시간: {summary.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)
        self.logger.info("검색 결과를 전송했습니다.")

    # 재요약 명령어
    @app_commands.command(name="요약다시", description="특정 요약본을 다시 요약합니다.")
    @app_commands.describe(summary_id="재요약할 요약 ID")
    async def resummarize(self, interaction: discord.Interaction, summary_id: str):
        self.logger.info(f"/요약다시 명령어 실행: 사용자={interaction.user}, 요약 ID={summary_id}")
        await interaction.response.defer(ephemeral=True)

        # 요약 ID 검증
        if not summary_id.isdigit():
            self.logger.error(f"유효하지 않은 요약 ID: {summary_id}")
            await interaction.followup.send("❌ 유효한 요약 ID가 아닙니다. 요약 ID는 숫자여야 합니다.", ephemeral=True)
            return

        # 데이터베이스에서 요약본 검색
        try:
            async with self.async_session() as session:
                stmt = select(SummaryModel).where(
                    SummaryModel.id == int(summary_id),
                    SummaryModel.user_id == str(interaction.user.id)
                )
                result = await session.execute(stmt)
                summary_doc = result.scalar_one_or_none()
            self.logger.info(f"검색된 요약본: {summary_doc}")
        except SQLAlchemyError as e:
            self.logger.error(f"데이터베이스 조회 중 오류: {e}")
            await interaction.followup.send("❌ 데이터베이스 조회 중 오류가 발생했습니다.", ephemeral=True)
            return

        if not summary_doc:
            self.logger.warning(f"요약본을 찾을 수 없음 또는 접근 권한 없음: ID={summary_id}")
            await interaction.followup.send("❌ 해당 요약본을 찾을 수 없거나 접근 권한이 없습니다.", ephemeral=True)
            return

        # 기존 요약을 기반으로 다시 요약 생성
        try:
            # 요약 수준 선택 (재요약은 간단하게 설정)
            new_summary = await self.process_summary(summary_doc.summary, SummaryLevel.SIMPLE)
            self.logger.info("재요약 생성 완료.")
            if not new_summary:
                self.logger.warning("재요약 내용이 비어있습니다.")
                await interaction.followup.send("⚠️ 재요약 내용이 비어있습니다.", ephemeral=True)
                return
        except Exception as e:
            self.logger.error(f"재요약 생성 중 오류: {e}")
            await interaction.followup.send(f"❌ 재요약 생성 중 오류가 발생했습니다: {e}", ephemeral=True)
            return

        # 임베드 생성 및 페이지 나누기
        summary_pages = self.split_text_into_pages(new_summary, max_length=2048)
        pages = []
        for page_content in summary_pages:
            embed = discord.Embed(
                title="📋 재요약본",
                description=page_content,
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"재요약 요청자: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
            pages.append(embed)

        channel = interaction.channel
        if len(pages) > 1:
            view = Summary.PaginationView(pages)
            message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
            view.message = message
        else:
            if isinstance(channel, discord.TextChannel):
                try:
                    thread = await interaction.channel.create_thread(
                        name=f"재요약-{interaction.user.display_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                        type=discord.ChannelType.private_thread,
                        invitable=False,
                        reason="Resummarized thread for user"
                    )
                    await thread.add_user(interaction.user)
                    close_view = Summary.CloseThreadView(thread, interaction.user.id, self.logger)
                    await thread.send(embed=pages[0], view=close_view)
                    self.logger.info(f"비공개 쓰레드 '{thread.name}'에 재요약을 전송했습니다.")

                    thread_url = thread.jump_url
                    await interaction.followup.send(f"✅ 비공개 스레드가 생성되었습니다: {thread_url}", ephemeral=True)

                except discord.Forbidden:
                    self.logger.error("비공개 쓰레드 생성 권한이 없습니다.")
                    await interaction.followup.send("❌ 비공개 쓰레드를 생성할 권한이 없습니다.", ephemeral=True)
                    return
                except discord.HTTPException as e:
                    self.logger.error(f"비공개 쓰레드 생성 또는 메시지 전송 중 HTTP 오류: {e}")
                    await interaction.followup.send("❌ 비공개 쓰레드 생성 또는 메시지 전송 중 오류가 발생했습니다.", ephemeral=True)
                    return
            else:
                try:
                    embed = pages[0]
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    self.logger.info("재요약 임베드를 직접 전송했습니다.")
                except discord.HTTPException as e:
                    self.logger.error(f"임베드 전송 중 HTTP 오류: {e}")
                    await interaction.followup.send("❌ 재요약 임베드를 전송하는 중 오류가 발생했습니다.", ephemeral=True)
                    return

        # 데이터베이스에 새 요약 저장 (텍스트 채널인 경우만 저장)
        if isinstance(channel, discord.TextChannel):
            await self.save_summary(
                summary_doc.guild_id,
                summary_doc.channel_id,
                interaction.user.id,
                summary_doc.start_time,
                summary_doc.end_time,
                new_summary
            )
        else:
            self.logger.info("비텍스트 채널에서 요약을 저장하지 않았습니다.")

    # 도움말 명령어
    @app_commands.command(name="도움", description="봇의 사용법을 안내합니다.")
    async def help_command(self, interaction: discord.Interaction):
        self.logger.info(f"/도움 명령어 실행: 사용자={interaction.user}")
        embed = discord.Embed(
            title="📖 요약봇 도움말",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="/요약",
            value=(
                "요약 과정을 단계적으로 진행합니다.\n"
                "1. 요약 수준을 선택하세요 (`간단`, `상세`).\n"
                "2. 요약할 시간대를 선택하세요.\n"
                "   - `지난 1시간`, `지난 24시간`, `오늘`, `어제`, `사용자 정의`.\n"
                "   - `사용자 정의`를 선택하면 시작 및 종료 날짜와 시간을 입력할 수 있습니다.\n"
                "3. 요약이 완료되면, 텍스트 채널에서는 비공개 스레드에, DM 등에서는 직접 요약을 받습니다."
            ),
            inline=False
        )
        embed.add_field(
            name="/회의록검색 [날짜]",
            value=(
                "특정 날짜의 요약본을 검색합니다.\n"
                "**예시**: `/회의록검색 2023-10-01`"
            ),
            inline=False
        )
        embed.add_field(
            name="/요약다시 [요약 ID]",
            value=(
                "특정 요약본을 다시 요약합니다.\n"
                "**예시**: `/요약다시 1`"
            ),
            inline=False
        )
        embed.set_footer(text="요약봇을 이용해 주셔서 감사합니다!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.logger.info("도움말 임베드 전송 완료.")

    @summarize.error
    async def summarize_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.followup.send(f"⚠️ 명령어 사용 제한에 도달했습니다. {round(error.retry_after, 2)}초 후에 다시 시도해주세요.", ephemeral=True)
            self.logger.warning(f"명령어 사용 제한: {error}")
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.followup.send("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            self.logger.warning(f"권한 부족: {error}")
        else:
            self.logger.error(f"요약 명령어 실행 중 오류: {error}")
            try:
                if not interaction.response.is_done():
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
            except discord.errors.NotFound:
                self.logger.error("웹훅을 찾을 수 없습니다. 에러 메시지를 전송할 수 없습니다.")

    @search_summaries.error
    async def search_summaries_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.followup.send(f"⚠️ 명령어 사용 제한에 도달했습니다. {round(error.retry_after, 2)}초 후에 다시 시도해주세요.", ephemeral=True)
            self.logger.warning(f"명령어 사용 제한: {error}")
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.followup.send("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            self.logger.warning(f"권한 부족: {error}")
        else:
            self.logger.error(f"회의록검색 명령어 실행 중 오류: {error}")
            try:
                if not interaction.response.is_done():
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
            except discord.errors.NotFound:
                self.logger.error("웹훅을 찾을 수 없습니다. 에러 메시지를 전송할 수 없습니다.")

    @resummarize.error
    async def resummarize_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.followup.send(f"⚠️ 명령어 사용 제한에 도달했습니다. {round(error.retry_after, 2)}초 후에 다시 시도해주세요.", ephemeral=True)
            self.logger.warning(f"명령어 사용 제한: {error}")
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.followup.send("❌ 이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            self.logger.warning(f"권한 부족: {error}")
        else:
            self.logger.error(f"재요약 명령어 실행 중 오류: {error}")
            try:
                if not interaction.response.is_done():
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ 명령어 실행 중 오류가 발생했습니다.", ephemeral=True)
            except discord.errors.NotFound:
                self.logger.error("웹훅을 찾을 수 없습니다. 에러 메시지를 전송할 수 없습니다.")

    @help_command.error
    async def help_command_error(self, interaction: discord.Interaction, error):
        self.logger.error(f"/도움 명령어 실행 중 오류: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.followup.send("❌ 도움말을 불러오는 중 오류가 발생했습니다.", ephemeral=True)
            else:
                await interaction.followup.send("❌ 도움말을 불러오는 중 오류가 발생했습니다.", ephemeral=True)
        except discord.errors.NotFound:
            self.logger.error("웹훅을 찾을 수 없습니다. 에러 메시지를 전송할 수 없습니다.")

    # 요약 생성 과정을 처리하는 메소드 (재요약에도 사용)
    async def process_summary(self, conversation: str, summary_level: SummaryLevel) -> str:
        self.logger.info("요약 처리 과정을 시작합니다.")
        
        # 설정된 최대 글자 수
        MAX_CHUNK_SIZE = 2000

        # 대화 내용 분할
        chunks = self.split_text_into_chunks(conversation, MAX_CHUNK_SIZE)
        self.logger.info(f"대화 내용을 {len(chunks)}개의 청크로 분할했습니다.")

        # 각 청크를 요약
        summarized_chunks = []
        for idx, chunk in enumerate(chunks, 1):
            self.logger.info(f"청크 {idx}/{len(chunks)} 요약 중...")
            prompt = f"다음 대화를 {summary_level.value}하게 요약해 주세요. 불필요한 번역이나 해석은 제외하고, 핵심 내용만 포함해 주세요:\n\n{chunk}"
            summarized_chunk = await self.generate_summary_gemini(prompt)
            if summarized_chunk:
                summarized_chunks.append(summarized_chunk)
            else:
                self.logger.warning(f"청크 {idx} 요약 결과가 비어있습니다.")

        # 청크 요약 결합
        combined_summary = "\n".join(summarized_chunks)
        self.logger.debug(f"결합된 청크 요약: {combined_summary}")

        # 최종 요약 생성
        self.logger.info("최종 요약 생성을 위해 결합된 요약을 다시 요약 중...")
        final_prompt = f"다음 요약들을 통합하여 전체 대화를 {summary_level.value}하게 요약해 주세요:\n\n{combined_summary}"
        final_summary = await self.generate_summary_gemini(final_prompt)
        self.logger.info("최종 요약 생성 완료.")
        return final_summary

    # 긴 텍스트를 청크로 분할하는 메소드
    def split_text_into_chunks(self, text: str, max_length: int) -> list:
        self.logger.debug("텍스트를 청크로 분할합니다.")
        chunks = []
        current_chunk = ""
        for line in text.split('\n'):
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk:
            chunks.append(current_chunk)
        self.logger.debug(f"텍스트 분할 완료: {len(chunks)}개의 청크")
        return chunks

    # 긴 텍스트를 페이지로 분할하는 메소드
    def split_text_into_pages(self, text: str, max_length: int = 2000) -> list:
        pages = []
        current_page = ""
        for line in text.split('\n'):
            if len(current_page) + len(line) + 1 > max_length:
                if current_page:
                    pages.append(current_page)
                current_page = line + "\n"
            else:
                current_page += line + "\n"
        if current_page.strip():
            pages.append(current_page.strip())
        return pages

    # Google Gemini API를 사용하여 요약 생성
    async def generate_summary_gemini(self, prompt: str) -> str:
        self.logger.info("Google Gemini API 호출을 시작합니다.")
        url_with_key = f"{self.gemini_api_url}?key={self.gemini_api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "prompt": {
                "text": prompt
            },
            "maxOutputTokens": 2048,
            "temperature": 0.7
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url_with_key, headers=headers, json=payload) as response:
                    response_text = await response.text()
                    self.logger.debug(f"Google Gemini API 응답: {response_text}")

                    if response.status == 200:
                        data = await response.json()
                        summary = data['candidates'][0]['output']['content'].strip()
                        self.logger.info("Google Gemini API 호출이 완료되었습니다.")
                        self.logger.debug(f"추출된 요약 내용: {summary}")
                        return summary
                    else:
                        self.logger.error(f"Google Gemini API 호출 오류: {response.status} - {response_text}")
                        raise Exception(f"Google Gemini API 호출 오류: {response.status} - {response_text}")
            except Exception as e:
                self.logger.error(f"Google Gemini API 호출 중 예외 발생: {e}")
                raise e

    # 요약본을 데이터베이스에 저장하는 메소드
    async def save_summary(self, guild_id, channel_id, user_id, start_time, end_time, summary):
        self.logger.info("데이터베이스에 요약 저장을 시도합니다.")
        self.logger.debug(f"start_time: {start_time}, tzinfo: {start_time.tzinfo}")
        self.logger.debug(f"end_time: {end_time}, tzinfo: {end_time.tzinfo}")
        self.logger.debug(f"summary: {summary}")
        created_at = datetime.now(timezone.utc)
        self.logger.debug(f"created_at: {created_at}, tzinfo: {created_at.tzinfo}")
        try:
            async with self.async_session() as session:
                async with session.begin():
                    new_summary = SummaryModel(
                        guild_id=str(guild_id),
                        channel_id=str(channel_id),
                        user_id=str(user_id),
                        start_time=start_time,
                        end_time=end_time,
                        summary=summary,
                        created_at=created_at
                    )
                    session.add(new_summary)
                await session.commit()
                await session.refresh(new_summary)
            self.logger.info(f"Summary saved with ID: {new_summary.id}")
        except SQLAlchemyError as e:
            self.logger.error(f"데이터베이스 저장 오류: {e}")
            raise e

    # 스레드를 닫기 위한 View 클래스
    class CloseThreadView(discord.ui.View):
        def __init__(self, thread, user_id, logger):
            super().__init__(timeout=None)
            self.thread = thread
            self.user_id = user_id
            self.logger = logger

        @discord.ui.button(label="스레드 닫기", style=discord.ButtonStyle.danger, emoji="🔒")
        async def close_thread(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("❌ 이 스레드를 닫을 권한이 없습니다.", ephemeral=True)
                return
            try:
                await self.thread.edit(archived=True, locked=True)
                await interaction.response.send_message("✅ 스레드가 성공적으로 닫혔습니다.", ephemeral=True)
                self.stop()
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                if interaction.message:
                    await interaction.message.edit(view=self)
                await self.thread.send(
                    f"🔒 이 스레드는 닫혔습니다.\n원래 채널로 돌아가려면 여기 클릭: <#{self.thread.parent.id}>"
                )

            except discord.Forbidden:
                self.logger.error("스레드를 수정할 권한이 없습니다.")
                await interaction.response.send_message("❌ 스레드를 수정할 권한이 없습니다.", ephemeral=True)
            except discord.HTTPException as e:
                self.logger.error(f"스레드 수정 중 HTTP 오류: {e}")
                await interaction.response.send_message("❌ 스레드를 수정하는 중 오류가 발생했습니다.", ephemeral=True)

    # 임베드 페이지네이션을 위한 View 클래스
    class PaginationView(discord.ui.View):
        def __init__(self, pages):
            super().__init__(timeout=300)
            self.pages = pages
            self.current_page = 0
            self.message = None

        @discord.ui.button(label="이전", style=discord.ButtonStyle.primary, emoji="⬅️")
        async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page > 0:
                self.current_page -= 1
                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
                await self.update_buttons()
            else:
                await interaction.response.send_message("이전 페이지가 없습니다.", ephemeral=True)

        @discord.ui.button(label="다음", style=discord.ButtonStyle.primary, emoji="➡️")
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
                await self.update_buttons()
            else:
                await interaction.response.send_message("다음 페이지가 없습니다.", ephemeral=True)

        async def update_buttons(self):
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.label == "이전":
                        child.disabled = self.current_page == 0
                    elif child.label == "다음":
                        child.disabled = self.current_page == len(self.pages) - 1
            if self.message:
                await self.message.edit(view=self)

        async def on_timeout(self):
            for item in self.children:
                item.disabled = True
            if self.message:
                await self.message.edit(view=self)

    @staticmethod
    async def setup(bot: commands.Bot):
        async_session = bot.async_session
        await bot.add_cog(Summary(bot, async_session))
