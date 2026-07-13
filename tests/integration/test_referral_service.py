"""Integration тесты для сервиса рефералов."""
import pytest
from datetime import datetime, timezone, timedelta


class TestReferralService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_process_bonus_success(self, test_db_session):
        from services.referral_service import ReferralService
        from database.models import User

        referrer = User(
            telegram_id=999999999,
            referral_days=0,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10),
        )
        test_db_session.add(referrer)
        await test_db_session.commit()

        await ReferralService.process_bonus(test_db_session, 111111111, 999999999)
        
        # 🔥 ИСПРАВЛЕНО: Коммитим изменения перед refresh
        await test_db_session.commit()
        await test_db_session.refresh(referrer)
        
        assert referrer.referral_days == 3  # REFERRAL_BONUS_DAYS = 3

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_process_bonus_referrer_not_found(self, test_db_session):
        from services.referral_service import ReferralService

        # Не должно упасть, если реферер не найден
        await ReferralService.process_bonus(test_db_session, 111111111, 999999999)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_process_bonus_extends_subscription(self, test_db_session):
        from services.referral_service import ReferralService
        from database.models import User

        referrer = User(
            telegram_id=888888888,
            referral_days=0,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5),
        )
        test_db_session.add(referrer)
        await test_db_session.commit()

        old_end = referrer.subscription_end

        await ReferralService.process_bonus(test_db_session, 222222222, 888888888)

        # 🔥 ИСПРАВЛЕНО: Коммитим изменения перед refresh
        await test_db_session.commit()
        await test_db_session.refresh(referrer)
        
        assert referrer.subscription_end > old_end
        assert referrer.referral_days == 3
