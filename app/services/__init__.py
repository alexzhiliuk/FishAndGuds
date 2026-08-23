from app.services.core import LoyaltyService, MailingService, NotificationService, PurchaseService, RestaurantService, SyncService
from app.services.iiko import CardService, CustomerService, LoyaltySyncService, RestaurantSyncService
from app.services.phone import PhoneNormalizationService
from app.services.registration import RegistrationService, RegistrationStart

__all__ = ["LoyaltyService", "PurchaseService", "RestaurantService", "MailingService", "NotificationService", "SyncService", "CardService", "CustomerService", "LoyaltySyncService", "RestaurantSyncService", "PhoneNormalizationService", "RegistrationService", "RegistrationStart"]
