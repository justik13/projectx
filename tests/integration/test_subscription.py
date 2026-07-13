"""Integration тесты для сервиса подписок."""
import pytest
from datetime import datetime, timezone, timedelta


class TestSubscriptionService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_access_active(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        # ИСПРАВЛЕНО: используем naive datetime (без timezone)
        user = User(
            telegram_id=123456789,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
            is_banned=False,
        )
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.check_access(test_db_session, 123456789)
        assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_access_expired(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        # ИСПРАВЛЕНО: используем naive datetime (без timezone)
        user = User(
            telegram_id=123456790,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.check_access(test_db_session, 123456790)
        assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_access_banned(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        user = User(
            telegram_id=123456791,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
            is_banned=True,
        )
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.check_access(test_db_session, 123456791)
        assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_access_no_subscription(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        user = User(telegram_id=123456792)
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.check_access(test_db_session, 123456792)
        assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_extend_subscription(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        user = User(telegram_id=123456796)
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.extend_subscription(
            test_db_session, 123456796, 30
        )

        assert result.subscription_end is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_extend_subscription_permanent(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        user = User(telegram_id=123456797)
        test_db_session.add(user)
        await test_db_session.commit()

        result = await SubscriptionService.extend_subscription(
            test_db_session, 123456797, 36500
        )

        assert result.subscription_end is not None
        assert result.subscription_end.year == 2100

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_process_onboarding_new_user(self, test_db_session):
        from services.subscription import SubscriptionService

        result = await SubscriptionService.process_onboarding(
            test_db_session, 123456800, "testuser", "Test User", None
        )

        assert result is not None
        assert result.telegram_id == 123456800

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_process_onboarding_with_referral(self, test_db_session):
        from services.subscription import SubscriptionService
        from database.models import User

        # Создаём реферера
        referrer = User(telegram_id=999999999)
        test_db_session.add(referrer)
        await test_db_session.commit()

        result = await SubscriptionService.process_onboarding(
            test_db_session, 123456801, "referred_user", "Referred", 999999999
        )

        assert result is not None
        assert result.referred_by == 999999999
