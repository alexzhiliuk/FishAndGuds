from aiogram.fsm.state import State, StatesGroup


class MailingCreate(StatesGroup):
    name = State()
    text = State()
    image = State()


class MailingEdit(StatesGroup):
    name = State()
    text = State()
    image = State()


class MailingSchedule(StatesGroup):
    when = State()


class BirthdayGreetingEdit(StatesGroup):
    text = State()
    image = State()
    send_time = State()


class RestaurantLinkEdit(StatesGroup):
    value = State()


class ApplicationDocumentEdit(StatesGroup):
    value = State()


class RegistrationForm(StatesGroup):
    mini_app = State()
