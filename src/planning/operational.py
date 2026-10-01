"""Оперативное управление обслуживанием (реальное время).

Реагирует на события, формирует предупреждения и рекомендации
по текущему состоянию парка.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger

from src.core.database import (NOT_DELIVERED, OPERATIONAL, RESERVE, Train,
                               MaintenanceEvent, get_session, hour_to_datetime)
from src.core.config import config
from src.core.entities import TrainState


class OperationalDispatcher:
    """Оперативный диспетчер"""

    def __init__(self, db_engine, as_of: datetime = None):
        self.engine = db_engine
        self.session = get_session(db_engine)
        # Часы симуляции, не настенные: иначе плановые даты 2028 года
        # никогда не попадают в «ближайшие 24 часа».
        self.as_of = as_of or hour_to_datetime(0)

    def check_critical_situations(self) -> List[Dict]:
        """Список предупреждений по поездам, готовности парка и загрузке депо."""
        alerts = []
        trains = self.session.query(Train).filter_by(status='operational').all()

        for train in trains:
            train_state = self._build_train_state(train)

            critical_services = self._check_critical_mileage(train, train_state)
            alerts.extend(critical_services)

            upcoming_services = self._check_upcoming_service(train, train_state)
            alerts.extend(upcoming_services)

        availability_alert = self._check_fleet_availability()
        if availability_alert:
            alerts.append(availability_alert)

        depot_alert = self._check_depot_overload()
        if depot_alert:
            alerts.append(depot_alert)

        return alerts

    def _build_train_state(self, train: Train) -> TrainState:
        """Состояние поезда по всем счётчикам пробега."""
        return TrainState.from_orm(train)

    def _check_critical_mileage(self, train: Train, state: TrainState) -> List[Dict]:
        alerts = []

        for service_type, trigger in config.MILEAGE_TRIGGERS.items():
            current = state.mileages.get(f'since_{service_type.lower()}', 0)

            # превышение порога более чем на 10%
            if current > trigger * 1.1:
                alerts.append({
                    'severity': 'CRITICAL',
                    'train_id': train.id,
                    'message': f'Поезд {train.number}: критическое превышение пробега для {service_type} '
                              f'({current:,.0f} км > {trigger * 1.1:,.0f} км)',
                    'action': 'НЕМЕДЛЕННО вывести на обслуживание',
                })
            # превышение на 5–10%
            elif current > trigger * 1.05:
                alerts.append({
                    'severity': 'HIGH',
                    'train_id': train.id,
                    'message': f'Поезд {train.number}: превышен пробег для {service_type} '
                              f'({current:,.0f} км)',
                    'action': 'Запланировать обслуживание в ближайшие 48 часов',
                })

        return alerts

    def _check_upcoming_service(self, train: Train, state: TrainState) -> List[Dict]:
        alerts = []

        for service_type, trigger in config.MILEAGE_TRIGGERS.items():
            current = state.mileages.get(f'since_{service_type.lower()}', 0)

            # 85–95% от порога
            if 0.85 * trigger < current < 0.95 * trigger:
                alerts.append({
                    'severity': 'INFO',
                    'train_id': train.id,
                    'message': f'Поезд {train.number}: приближается {service_type} '
                              f'(пробег {current:,.0f} / {trigger:,.0f} км)',
                    'action': 'Зарезервировать слот в депо',
                })

        return alerts

    def _check_fleet_availability(self) -> Optional[Dict]:
        total = self.session.query(Train).filter(Train.status != NOT_DELIVERED).count()
        operational = self.session.query(Train).filter(
            Train.status.in_([OPERATIONAL, RESERVE])).count()

        if total == 0:
            return None

        availability = operational / total

        if availability < config.TARGET_AVAILABILITY:
            return {
                'severity': 'CRITICAL',
                'train_id': None,
                'message': f'Готовность парка {availability:.1%} ниже целевой {config.TARGET_AVAILABILITY:.0%}',
                'action': 'Ускорить обслуживание или активировать резервные поезда',
            }

        return None

    def _check_depot_overload(self) -> Optional[Dict]:
        now = self.as_of
        next_24h = now + timedelta(hours=24)

        active_events = self.session.query(MaintenanceEvent).filter(
            MaintenanceEvent.status.in_(['planned', 'in_progress']),
            MaintenanceEvent.scheduled_start <= next_24h,
            MaintenanceEvent.scheduled_end >= now
        ).count()

        if active_events >= config.DEPOT_CAPACITY:
            return {
                'severity': 'HIGH',
                'train_id': None,
                'message': f'Депо перегружено: {active_events}/{config.DEPOT_CAPACITY} позиций занято',
                'action': 'Отложить некритичные обслуживания',
            }

        return None

    def suggest_action(self) -> str:
        """Текстовая рекомендация: что сделать прямо сейчас."""
        alerts = self.check_critical_situations()

        critical = [a for a in alerts if a['severity'] == 'CRITICAL']
        if critical:
            return critical[0]['action']

        high = [a for a in alerts if a['severity'] == 'HIGH']
        if high:
            return high[0]['action']

        depot_utilization = self._calculate_depot_utilization()

        if depot_utilization < 0.5:  # депо загружено менее чем на 50%
            trains = self.session.query(Train).filter_by(status='operational').all()
            candidates = []

            for train in trains:
                state = self._build_train_state(train)
                services = state.services_due(urgency_threshold=0.85)

                if services:
                    candidates.append((train, services[0]))

            if candidates:
                train, service = candidates[0]
                return (f"Рекомендация: отправить поезд {train.number} на "
                       f"{service} (депо свободно, пробег {state.mileages.get(f'since_{service.lower()}', 0):,.0f} км)")

        return "Продолжить нормальную эксплуатацию"

    def _calculate_depot_utilization(self) -> float:
        now = self.as_of

        active = self.session.query(MaintenanceEvent).filter(
            MaintenanceEvent.status == 'in_progress',
            MaintenanceEvent.actual_start <= now,
            MaintenanceEvent.scheduled_end >= now
        ).count()

        return active / config.DEPOT_CAPACITY

    def handle_breakdown(self, train_id: int, issue: str, estimated_hours: float = 24):
        """Вывести поезд из эксплуатации и создать срочное событие ремонта."""
        logger.warning(f"Внеплановая поломка поезда ID={train_id}: {issue}")

        train = self.session.get(Train, train_id)
        if not train:
            logger.error(f"Поезд ID={train_id} не найден")
            return

        train.status = 'maintenance'

        now = self.as_of
        event = MaintenanceEvent(
            train_id=train_id,
            service_type='emergency_repair',
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=estimated_hours),
            planned_duration_hours=estimated_hours,
            status='in_progress',
            reason='breakdown',
            work_description=issue
        )

        self.session.add(event)
        self.session.commit()

        logger.info(f"Поезд {train.number} выведен из эксплуатации, создано событие ремонта")

        availability_alert = self._check_fleet_availability()
        if availability_alert:
            logger.warning(availability_alert['message'])
