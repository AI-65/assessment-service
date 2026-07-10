<?php

declare(strict_types=1);

namespace Edutiek\AssessmentService\Assessment\Api;

use Edutiek\AssessmentService\Assessment\Alert\Service as AlertService;
use Edutiek\AssessmentService\Assessment\AppBridges\CorrectorBridge;
use Edutiek\AssessmentService\Assessment\Apps\AppBridge;
use Edutiek\AssessmentService\Assessment\Apps\OpenHelper;
use Edutiek\AssessmentService\Assessment\Apps\RestHelper;
use Edutiek\AssessmentService\Assessment\Apps\Service as AppService;
use Edutiek\AssessmentService\Assessment\AssessmentGrading\Service as AssessmentGradingService;
use Edutiek\AssessmentService\Assessment\Authentication\Service as AuthenticationService;
use Edutiek\AssessmentService\Assessment\BackgroundTask\Service as BackgroundTaskService;
use Edutiek\AssessmentService\Assessment\ConstraintHandling\Provider as ConstraintProvider;
use Edutiek\AssessmentService\Assessment\CorrectionProcess\Service as CorrectionProcessService;
use Edutiek\AssessmentService\Assessment\CorrectionSettings\Service as CorrectionSettingsService;
use Edutiek\AssessmentService\Assessment\Corrector\Service as CorrectorService;
use Edutiek\AssessmentService\Assessment\Apps\AppCorrector;
use Edutiek\AssessmentService\Assessment\Apps\AppProvider;
use Edutiek\AssessmentService\Assessment\Data\OrgaSettings;
use Edutiek\AssessmentService\Assessment\DisabledGroup\Service as DisabledGroupService;
use Edutiek\AssessmentService\Assessment\EventHandling\AssessmentObserver as AssessmentObserver;
use Edutiek\AssessmentService\Assessment\EventHandling\SystemObserver as SystemObserver;
use Edutiek\AssessmentService\Assessment\Export\DocumentationExport;
use Edutiek\AssessmentService\Assessment\Export\Service as ExportService;
use Edutiek\AssessmentService\Assessment\Format\FullService as FormatInterface;
use Edutiek\AssessmentService\Assessment\Format\Service as Format;
use Edutiek\AssessmentService\Assessment\FileUsage\Finder;
use Edutiek\AssessmentService\Assessment\GradeLevel\Service as GradeLevelService;
use Edutiek\AssessmentService\Assessment\Location\Service as LocationService;
use Edutiek\AssessmentService\Assessment\LogEntry\Service as LogEntryService;
use Edutiek\AssessmentService\Assessment\Manager\Service as ManagerService;
use Edutiek\AssessmentService\Assessment\Notification\ReviewNotificationsHandler;
use Edutiek\AssessmentService\Assessment\Notification\Service as NotificationService;
use Edutiek\AssessmentService\Assessment\OrgaSettings\Service as OrgaSettingsService;
use Edutiek\AssessmentService\Assessment\PdfCreation\CorrectionReport;
use Edutiek\AssessmentService\Assessment\PdfCreation\PdfPartProvider;
use Edutiek\AssessmentService\Assessment\PdfCreation\Service as PdfCreationService;
use Edutiek\AssessmentService\Assessment\PdfSettings\Service as PdfSettingsService;
use Edutiek\AssessmentService\Assessment\Permissions\Service as PermissionsService;
use Edutiek\AssessmentService\Assessment\Properties\Service as PropertiesService;
use Edutiek\AssessmentService\Assessment\Pseudonym\FullService as PseudonymFullService;
use Edutiek\AssessmentService\Assessment\Pseudonym\Service as PseudonymService;
use Edutiek\AssessmentService\Assessment\TaskInterfaces\TaskType;
use Edutiek\AssessmentService\Assessment\WorkingTime\Factory as WorkingTimeFactory;
use Edutiek\AssessmentService\Assessment\Writer\Service as WriterService;
use Edutiek\AssessmentService\Assessment\WritingTask\Service as WritingTaskService;
use Edutiek\AssessmentService\Assessment\AppBridges\WriterBridge;
use Edutiek\AssessmentService\System\Language\FullService as LanguageService;
use Slim\App;
use Slim\Factory\AppFactory;
use Edutiek\AssessmentService\Assessment\PdfCreation\CorrectionProvider;
use Edutiek\AssessmentService\Assessment\Apps\AppWriter;
use Edutiek\AssessmentService\Assessment\Apps\AppCorrectorBridge;
use Edutiek\AssessmentService\Assessment\Export\ResultsExport;
use Edutiek\AssessmentService\Assessment\Apps\SlimLogger;
use Edutiek\AssessmentService\System\File\FileUsageFinder;
use Edutiek\AssessmentService\Assessment\FileUsage\FileCleanupHandler;

class Internal implements ComponentApi, ComponentApiFactory
{
    private array $instances = [];

    public function __construct(
        private readonly Dependencies $dependencies
    ) {
    }

    public function allComponents(): array
    {
        $components = ['Assessment', 'Task'];
        foreach (TaskType::cases() as $task_type) {
            $components[] = $task_type->component();
        }
        return $components;
    }

    public function components(int $ass_id, int $user_id): array
    {
        $components = ['Assessment', 'Task'];
        foreach ($this->dependencies->taskApi()->taskManager($ass_id, $user_id)->all() as $task) {
            $components[] = $task->getTaskType()->component();
        }
        return array_unique($components);
    }

    public function api(string $component): ?ComponentApi
    {
        $compare = strtolower($component);
        switch ($compare) {
            case 'assessment':
                return $this;
            case 'task':
                return $this->dependencies->taskApi();
            default:
                foreach (TaskType::cases() as $task_type) {
                    if (strtolower($task_type->component()) === $compare) {
                        return  $this->dependencies->typeApis()->api($task_type);
                    }
                }
        }
        return null;
    }

    /**
     * Common service for all web apps
     * (no caching needed, created once per request)
     */
    public function appService(): AppService
    {
        return $this->instances[AppService::class] ??= new AppService(
            $this->dependencies->systemApi()->config(),
            $this->dependencies->restContext(),
            $this
        );
    }

    /**
     * REST handler for corrector web app
     * (no caching needed, created once per request)
     */
    public function appCorrector(int $ass_id, int $context_id, int $user_id): AppCorrector
    {
        return new AppCorrector(
            $ass_id,
            $context_id,
            $user_id,
            $this->permissions($ass_id, $context_id, $user_id),
            $this->restHelper($ass_id, $context_id, $user_id),
            $this,
            $this->slimApp(),
            $this->dependencies->restContext(),
            $this->dependencies->systemApi()->fileDelivery()
        );
    }

    /**
     * REST handler for external correction providers
     * (no caching needed, created once per request)
     */
    public function appProvider(int $ass_id, int $context_id, int $user_id): AppProvider
    {
        return new AppProvider(
            $ass_id,
            $context_id,
            $user_id,
            $this->permissions($ass_id, $context_id, $user_id),
            $this->restHelper($ass_id, $context_id, $user_id),
            $this,
            $this->slimApp(),
            $this->dependencies->restContext(),
            $this->dependencies->systemApi()->fileDelivery()
        );
    }

    /**
     * REST handler for writer web app
     * (no caching needed, created once per request)
     */
    public function appWriter(int $ass_id, int $context_id, int $user_id): AppWriter
    {
        return new AppWriter(
            $ass_id,
            $context_id,
            $user_id,
            $this->permissions($ass_id, $context_id, $user_id),
            $this->restHelper($ass_id, $context_id, $user_id),
            $this,
            $this->slimApp(),
            $this->dependencies->restContext(),
            $this->dependencies->systemApi()->fileDelivery()
        );
    }

    /**
     * Internal authentication service for REST handlers
     */
    public function authentication(int $ass_id, int $context_id): AuthenticationService
    {
        return $this->instances[AuthenticationService::class][$ass_id][$context_id] ??= new AuthenticationService(
            $ass_id,
            $context_id,
            $this->dependencies->repositories()
        );
    }

    public function backgroundTasks(int $ass_id, int $context_id, int $user_id): BackgroundTaskService
    {
        return $this->instances[BackgroundTaskService::class] ??= new BackgroundTaskService(
            $ass_id,
            $context_id,
            $user_id,
            $this->properties($ass_id),
            $this->language($user_id),
            $this->dependencies->systemApi()->backgroundTask(),
            $this->dependencies->systemApi()->fileStorage(),
            $this
        );
    }

    public function logEntry(int $ass_id, bool $temporary_export = true): LogEntryService
    {
        return $this->instances[LogEntryService::class][$ass_id][$temporary_export] ??= new LogEntryService(
            $ass_id,
            $this->dependencies->repositories(),
            // set user_id 0 to use the system default language
            $this->language(0),
            $this->dependencies->systemApi()->format(0),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->systemApi()->spreadsheet($temporary_export)
        );
    }

    /**
     * Service for querying permissions
     */
    public function permissions(int $ass_id, int $context_id, int $user_id): PermissionsService
    {
        return $this->instances[PermissionsService::class][$ass_id][$context_id][$user_id] ??= new PermissionsService(
            $ass_id,
            $context_id,
            $user_id,
            $this->dependencies->repositories(),
            $this->workingTimeFactory($user_id)
        );
    }

    public function properties(int $ass_id): PropertiesService
    {
        return $this->instances[PropertiesService::class][$ass_id] ??= new PropertiesService(
            $ass_id,
            $this->dependencies->repositories()
        );
    }

    /**
     * Service for creating pseudonyms
     */
    public function pseudonym(int $ass_id): PseudonymFullService
    {
        return $this->instances[PseudonymService::class][$ass_id] ??= new PseudonymService(
            $ass_id,
            $this->dependencies->repositories(),
            // set user_id 0 to use the system default language
            $this->language(0),
            $this->dependencies->systemApi()->user()
        );
    }

    public function writer(int $ass_id, int $user_id): WriterService
    {
        return $this->instances[WriterService::class][$ass_id] ??= new WriterService(
            $ass_id,
            $user_id,
            $this->dependencies->repositories(),
            $this->workingTimeFactory($user_id),
            $this->logEntry($ass_id),
            $this->notification($ass_id, $user_id),
            $this->pseudonym($ass_id),
            $this->dependencies->eventDispatcher($ass_id, $user_id),
        );
    }

    /**
     * Translation of language variables
     */
    public function language(int $user_id): LanguageService
    {
        return $this->dependencies->systemApi()->language($user_id, __DIR__ . '/../Languages/');
    }

    /**
     * Helper functions to open the WebApps
     * (no caching needed, created once per request)
     */
    public function openHelper(int $ass_id, int $context_id, int $user_id): OpenHelper
    {
        return new openHelper(
            $ass_id,
            $context_id,
            $user_id,
            $this->authentication($ass_id, $context_id),
            $this->dependencies->systemApi()->config()
        );
    }

    /**
     * Helper functions for the REST services
     * (no caching needed, created once per request)
     */
    public function restHelper(int $ass_id, int $context_id, int $user_id): RestHelper
    {
        return new RestHelper(
            $ass_id,
            $context_id,
            $user_id,
            $this->authentication($ass_id, $context_id),
            $this->permissions($ass_id, $context_id, $user_id),
            $this->dependencies->repositories(),
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->systemApi()->fileDelivery(),
            $this->dependencies->restContext()
        );
    }

    public function documentationExport(int $ass_id, int $context_id, int $user_id): DocumentationExport
    {
        return $this->instances[DocumentationExport::class][$ass_id][$context_id][$user_id] ??= new DocumentationExport(
            $ass_id,
            $this->dependencies->repositories(),
            $this->properties($ass_id),
            $this->writer($ass_id, $user_id),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->pdfCreation($ass_id, $context_id, $user_id),
            $this->writingTask($ass_id, $user_id),
            $this->correctionSettings($ass_id, $user_id),
            $this->logEntry($ass_id),
            $this->resultsExport($ass_id, $context_id, $user_id),
            $this->language($user_id),
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->spreadsheet(false),
            $this->dependencies->systemApi()->fileStorage(),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->systemApi()->format($user_id)
        );
    }

    public function resultsExport(int $ass_id, int $context_id, int $user_id): ResultsExport
    {
        return $this->instances[ResultsExport::class][$ass_id][$context_id][$user_id] ??= new ResultsExport(
            $ass_id,
            $context_id,
            $user_id,
            $this->dependencies->repositories(),
            $this->correctionSettings($ass_id, $user_id)->get(),
            $this->writer($ass_id, $user_id),
            $this->corrector($ass_id, $user_id),
            $this->assessmentGrading($ass_id),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->dependencies->taskApi()->gradingProvider($ass_id, $user_id),
            $this->language($user_id),
            $this->format($ass_id, $user_id),
            $this->dependencies->systemApi()->spreadsheet(false),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->systemApi()->format($user_id),
        );
    }

    /**
     * Configured slim app instance
     * (no caching needed, created once per request)
     */
    private function slimApp(): App
    {
        $app = AppFactory::create();
        $app->addRoutingMiddleware();
        $app->addErrorMiddleware(
            true,
            true,
            true,
            new SlimLogger($this->dependencies->systemApi()->log())
        );
        $app->setBasePath(dirname(parse_url(
            $this->dependencies->systemApi()->config()->getSetup()->getBackendUrl(),
            PHP_URL_PATH
        )));
        return $app;
    }

    public function writerBridge(int $ass_id, int $user_id): ?AppBridge
    {
        return $this->instances[WriterBridge::class][$ass_id][$user_id] ??= new WriterBridge(
            $ass_id,
            $user_id,
            $this->workingTimeFactory($user_id),
            $this->writer($ass_id, $user_id),
            $this->alert($ass_id),
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->entity(),
            $this->dependencies->repositories(),
        );
    }

    /**
     * Factory for working time calculations
     */
    public function workingTimeFactory(int $user_id): WorkingTimeFactory
    {
        return $this->instances[WorkingTimeFactory::class][$user_id] ??= new WorkingTimeFactory(
            $this->language($user_id),
        );
    }

    public function assessmentGrading(int $ass_id): AssessmentGradingService
    {
        return $this->instances[AssessmentGradingService::class][$ass_id] ??= new AssessmentGradingService($ass_id, $this->dependencies->repositories());
    }

    public function correctionProcess(int $ass_id, int $user_id): CorrectionProcessService
    {
        return $this->instances[CorrectionProcessService::class][$ass_id][$user_id] ??= new CorrectionProcessService(
            $ass_id,
            $user_id,
            $this->dependencies->repositories(),
            $this->correctionSettings($ass_id, $user_id),
            $this->writer($ass_id, $user_id),
            $this->corrector($ass_id, $user_id),
            $this->notification($ass_id, $user_id),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->dependencies->taskApi()->gradingProvider($ass_id, $user_id)
        );
    }

    public function correctionSettings(int $ass_id, int $user_id): CorrectionSettingsService
    {
        return $this->instances[CorrectionSettingsService::class][$ass_id][$user_id] ??= new CorrectionSettingsService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->pseudonym($ass_id),
            $this->language($user_id)
        );
    }

    public function corrector(int $ass_id, int $user_id): CorrectorService
    {
        return $this->instances[CorrectorService::class][$ass_id] ??= new CorrectorService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->dependencies->eventDispatcher($ass_id, $user_id),
        );
    }

    public function correctorBridge(int $ass_id, int $user_id): ?AppCorrectorBridge
    {
        return $this->instances[CorrectorBridge::class][$ass_id][$user_id] ??= new CorrectorBridge(
            $ass_id,
            $user_id,
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->entity(),
            $this->dependencies->repositories(),
        );
    }

    public function correctionPartProvider(int $ass_id, int $context_id, int $user_id): ?PdfPartProvider
    {
        return $this->instances[CorrectionProvider::class][$ass_id][$user_id] ?? new CorrectionProvider(
            $ass_id,
            $user_id,
            $context_id,
            $this->dependencies->repositories(),
            $this->orgaSettings($ass_id, $user_id)->get(),
            $this->pdfSettings($ass_id)->get(),
            $this->correctionSettings($ass_id, $user_id)->get(),
            $this->assessmentGrading($ass_id),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->dependencies->taskApi()->gradingProvider($ass_id, $user_id),
            $this->dependencies->systemApi()->htmlProcessing(),
            $this->dependencies->systemApi()->pdfProcessing(),
            $this->language($user_id),
            $this->dependencies->systemApi()->format($user_id),
            $this->dependencies->systemApi()->user()
        );
    }

    public function correctionReport(int $ass_id, int $user_id): CorrectionReport
    {
        return $this->instances[CorrectionReport::class][$ass_id][$user_id] ?? new CorrectionReport(
            $ass_id,
            $this->dependencies->repositories(),
            $this->dependencies->systemApi()->htmlProcessing(),
            $this->dependencies->systemApi()->pdfProcessing(),
            $this->language($user_id),
        );
    }

    public function gradeLevel(int $ass_id): GradeLevelService
    {
        return $this->instances[GradeLevelService::class][$ass_id] = new GradeLevelService(
            $ass_id,
            $this->dependencies->repositories()
        );
    }

    public function manager(int $ass_id, int $user_id): ManagerService
    {
        return $this->instances[ManagerService::class][$ass_id][$user_id] = new ManagerService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->language($user_id),
            $this->dependencies->systemApi()->fileStorage(),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id)
        );
    }

    public function location(int $ass_id): LocationService
    {
        return $this->instances[LocationService::class][$ass_id] = new LocationService(
            $ass_id,
            $this->dependencies->repositories()
        );
    }

    public function notification(int $ass_id, int $user_id): NotificationService
    {
        return $this->instances[NotificationService::class][$ass_id][$user_id] = new NotificationService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->orgaSettings($ass_id, $user_id),
            $this->correctionSettings($ass_id, $user_id),
            $this->language($user_id),
            $this->dependencies->systemApi()->mailDelivery(),
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->user()
        );
    }

    public function reviewNotificationHandler(int $user_id): CronHandler
    {
        return $this->instances[ReviewNotificationsHandler::class][$user_id] = new ReviewNotificationsHandler(
            $user_id,
            $this,
            $this->dependencies->repositories(),
        );
    }

    public function orgaSettings(int $ass_id, int $user_id): OrgaSettingsService
    {
        return $this->instances[OrgaSettingsService::class][$ass_id][$user_id] = new OrgaSettingsService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->workingTimeFactory($user_id),
            $this->language($user_id)
        );
    }

    public function pdfCreation(int $ass_id, int $context_id, int $user_id): PdfCreationService
    {
        return $this->instances[PdfCreationService::class][$ass_id][$context_id][$user_id] = new PdfCreationService(
            $ass_id,
            $context_id,
            $user_id,
            $this,
            $this->writer($ass_id, $user_id),
            $this->dependencies->repositories(),
            $this->pdfSettings($ass_id)->get(),
            $this->dependencies->systemApi()->pdfProcessing(),
            $this->dependencies->systemApi()->config(),
            $this->dependencies->systemApi()->fileStorage(),
            $this->language($user_id),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->properties($ass_id),
            $this->correctionReport($ass_id, $user_id)
        );
    }

    public function pdfSettings(int $ass_id): PdfSettingsService
    {
        return $this->instances[PdfSettingsService::class][$ass_id] = new PdfSettingsService(
            $ass_id,
            $this->dependencies->repositories()
        );
    }

    public function format(int $ass_id, int $user_id): FormatInterface
    {
        return $this->instances[Format::class][$ass_id][$user_id] = new Format(
            $this->language($user_id),
            $this->dependencies->systemApi()->user(),
            $this->dependencies->systemApi()->format($user_id),
            $this->assessmentGrading($ass_id),
            $this->orgaSettings($ass_id, $user_id)->get()
        );
    }

    public function alert(int $ass_id): AlertService
    {
        return $this->instances[AlertService::class][$ass_id] ??= new AlertService(
            $ass_id,
            $this->dependencies->repositories()
        );
    }

    public function disabledGroup(int $ass_id): DisabledGroupService
    {
        return $this->instances[DisabledGroupService::class][$ass_id] ??= new DisabledGroupService(
            $ass_id,
            $this->dependencies->repositories()->disabledGroup()
        );
    }

    public function constraintProvider(int $ass_id, int $user_id): ConstraintProvider
    {
        return $this->instances[ConstraintProvider::class][$ass_id][$user_id] ??= new ConstraintProvider(
            $ass_id,
            $user_id,
            $this
        );
    }

    public function assessmentObserver(int $ass_id, int $user_id): AssessmentObserver
    {
        return $this->instances[AssessmentObserver::class][$ass_id][$user_id] ??= new AssessmentObserver(
            $ass_id,
            $user_id,
            $this,
            $this->dependencies->repositories()
        );
    }

    public function systemObserver(int $user_id): SystemObserver
    {
        return $this->instances[SystemObserver::class][$user_id] ??= new SystemObserver(
            $user_id,
            $this,
            $this->dependencies->repositories()
        );
    }

    public function export(int $ass_id, int $context_id, int $user_id): ExportService
    {
        return $this->instances[ExportService::class][$ass_id][$context_id][$user_id] ??= new ExportService(
            $ass_id,
            $this->dependencies->repositories(),
            $this->pdfCreation($ass_id, $context_id, $user_id),
            $this->backgroundTasks($ass_id, $context_id, $user_id),
            $this->dependencies->systemApi()->fileStorage(),
            $this->dependencies->systemApi()->fileDelivery(),
            $this->resultsExport($ass_id, $context_id, $user_id),
            $this->logEntry($ass_id, false)
        );
    }

    public function writingPartProvider(int $ass_id, int $context_id, int $user_id): ?PdfPartProvider
    {
        // currently the assessment component provides no writing parts
        return null;
    }

    public function writingTask(int $ass_id, int $user_id): WritingTaskService
    {
        return  $this->instances[WritingTaskService::class][$ass_id][$user_id] = new WritingTaskService(
            $this->dependencies->taskApi()->taskManager($ass_id, $user_id),
            $this->writer($ass_id, $user_id)
        );
    }

    public function fileUsageFinder(): FileUsageFinder
    {
        return  $this->instances[Finder::class] = new Finder(
            $this->dependencies->repositories()
        );
    }

    public function fileCleanupHandler($user_id): FileCleanupHandler
    {
        return $this->instances[FileCleanupHandler::class] = new FileCleanupHandler(
            $this->dependencies->systemApi()->fileStorage(),
            $this->dependencies->systemApi()->tempStorage(),
            array_map(fn(string $component) => $this->api($component)->fileUsageFinder(), $this->allComponents()),
            $this->language($user_id)
        );
    }
}
