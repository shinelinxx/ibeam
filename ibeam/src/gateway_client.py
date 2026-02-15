import logging
import sys
import time

from pathlib import Path
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ibeam.src.health_server import new_health_server
from ibeam.src.handlers.http_handler import HttpHandler, Status
from ibeam.src.handlers.process_handler import ProcessHandler
from ibeam.src.handlers.strategy_handler import StrategyHandler

sys.path.insert(0, str(Path(__file__).parent.parent))

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)

class GatewayClient():

    def __init__(self,
                 http_handler: HttpHandler,
                 strategy_handler: StrategyHandler,
                 process_handler: ProcessHandler,
                 health_server_port: int,
                 spawn_new_processes: bool,
                 maintenance_interval: int,
                 request_retries: int,
                 active:bool=True,
                 ):

        self._should_shutdown = False

        self.http_handler = http_handler
        self.strategy_handler = strategy_handler
        self.process_handler = process_handler

        self.health_server_port = health_server_port
        self.spawn_new_processes = spawn_new_processes
        self.maintenance_interval = maintenance_interval
        self.request_retries = request_retries

        self._concurrent_maintenance_attempts = 1
        self._authenticating = False
        self._health_server = new_health_server(
            self.health_server_port,
            self.http_handler.get_status,
            self.get_shutdown_status,
            self.on_activate,
            self.on_deactivate,
            self.on_authenticate,
            self.get_status_json,
        )

        self._active = active

    def get_shutdown_status(self) -> bool:
        return self._should_shutdown

    def get_status_json(self) -> dict:
        """Return current ibeam status as a JSON-serializable dict."""
        try:
            status = self.http_handler.get_status()
            return {
                'active': self._active,
                'authenticating': self._authenticating,
                'shutdown': self._should_shutdown,
                'authenticated': status.authenticated if status else False,
                'connected': status.connected if status else False,
                'competing': status.competing if status else False,
                'session': status.session if status else False,
                'serverName': status.server_name if status else None,
                'sessionId': status.session_id if status else None,
            }
        except Exception as e:
            _LOGGER.debug(f'get_status_json error: {e}')
            return {
                'active': self._active,
                'authenticating': self._authenticating,
                'shutdown': self._should_shutdown,
                'authenticated': False,
                'connected': False,
                'competing': False,
                'session': False,
                'serverName': None,
                'sessionId': None,
            }

    def start_and_authenticate(self, request_retries=1) -> (bool, bool, Status):
        """Starts the gateway and authenticates using the credentials stored."""

        self.process_handler.start_gateway()

        success, shutdown, status = self.strategy_handler.try_authenticating(request_retries=request_retries)
        self._should_shutdown = shutdown
        return success, shutdown, status

    def on_authenticate(self) -> dict:
        """Manually trigger authentication. Activates if dormant, then authenticates immediately.

        Unlike maintenance-triggered auth, manual auth:
        - Does NOT propagate shutdown (wrong password shouldn't kill ibeam)
        - Resets the failed-auth counter so user can retry after fixing credentials
        - Resets shutdown state if previously shut down
        """
        _LOGGER.info('Manual authentication triggered')

        if self._authenticating:
            _LOGGER.info('Authentication already in progress, skipping')
            return {'success': False, 'message': 'Authentication already in progress'}

        if not self._active:
            _LOGGER.info('Activating before manual authentication')
            self._active = True

        # Reset shutdown state so manual auth can recover from previous failures
        if self._should_shutdown:
            _LOGGER.info('Resetting shutdown state for manual authentication')
            self._should_shutdown = False

        # Reset the login_handler's failed attempt counter
        self.strategy_handler.login_handler.failed_attempts = 0

        self._authenticating = True
        try:
            success, shutdown, status = self.start_and_authenticate(request_retries=self.request_retries)

            if shutdown:
                # Don't propagate shutdown from manual auth — just report the error
                self._should_shutdown = False
                _LOGGER.warning('Manual authentication hit max failed attempts, but shutdown is suppressed for manual mode')
                # Reset counter again so next manual attempt is possible
                self.strategy_handler.login_handler.failed_attempts = 0
                return {'success': False, 'message': 'Authentication failed: invalid credentials or max attempts reached'}
            elif success:
                _LOGGER.info(f'Manual authentication successful, session id: {status.session_id}')
                self.http_handler.validate()
                return {'success': True, 'message': 'Authenticated successfully'}
            else:
                return {'success': False, 'message': 'Authentication failed'}
        except Exception as e:
            _LOGGER.error(f'Manual authentication error: {e}')
            return {'success': False, 'message': str(e)}
        finally:
            self._authenticating = False

    def on_activate(self) -> bool:
        if self._active:
            return True

        _LOGGER.info('Activating')
        self._active = True
        return True

    def on_deactivate(self) -> bool:
        if not self._active:
            return True

        _LOGGER.info('Deactivating')
        self._active = False
        self.http_handler.logout()
        self.process_handler.kill_gateway()
        return True

    def build_scheduler(self):
        if self.spawn_new_processes:
            executors = {'default': ProcessPoolExecutor(self._concurrent_maintenance_attempts)}
        else:
            executors = {'default': ThreadPoolExecutor(self._concurrent_maintenance_attempts)}
        job_defaults = {'coalesce': False, 'max_instances': self._concurrent_maintenance_attempts}
        self._scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults, timezone='UTC')
        self._scheduler.add_job(self._maintenance, trigger=IntervalTrigger(seconds=self.maintenance_interval))

    def maintain(self):
        self.build_scheduler()
        _LOGGER.info(f'Starting maintenance with interval {self.maintenance_interval} seconds')
        self._scheduler.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt as e:
            _LOGGER.info('Keyboard interrupt, shutting down.')
            pass
        self._scheduler.remove_all_jobs()
        self._scheduler.shutdown(wait=False)
        if self._health_server:
            self._health_server.shutdown()

    def _maintenance(self):
        if not self._active:
            _LOGGER.info('Maintenance skipped, GatewayClient is not active.')
            return

        if self._authenticating:
            _LOGGER.info('Maintenance skipped, authentication in progress.')
            return

        _LOGGER.info('Maintenance')

        self._authenticating = True
        try:
            success, shutdown, status = self.start_and_authenticate(request_retries=self.request_retries)
        finally:
            self._authenticating = False

        if shutdown:
            _LOGGER.warning('Shutting IBeam down due to critical error.')
            self._scheduler.remove_all_jobs()
            self._scheduler.shutdown(False)
            if self._health_server:
                self._health_server.shutdown()
        elif success:
            _LOGGER.info(f'Gateway running and authenticated, session id: {status.session_id}, server name: {status.server_name}')
            validate_success = self.http_handler.validate()
            if not validate_success:
                _LOGGER.warning(f'Validation result is False when IBeam attempted to extend the SSO token. This could indicate token authentication issues.')

    def shutdown(self):
        if hasattr(self, '_health_server') and self._health_server:
            self._health_server.shutdown()

    def __getstate__(self):
        state = self.__dict__.copy()

        # APS schedulers and health_server can't be pickled
        state.pop('_scheduler', None)
        state.pop('_health_server', None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.build_scheduler()

    @property
    def active(self):
        return self._active
