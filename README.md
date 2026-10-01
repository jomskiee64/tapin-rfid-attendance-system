# TAP-IN RFID Attendance System

TAP-IN is an RFID-based employee attendance management system that combines an IoT RFID device, web dashboard, Flask API, and PostgreSQL database.

The system is designed to automate employee attendance recording through RFID scanning while providing an interface for managing employees, monitoring attendance, and monitoring connected IoT devices.

## Features

* RFID-based employee identification
* Automated attendance recording
* Employee management
* RFID registration and management
* Employee profile management
* Attendance and DTR monitoring
* Dashboard statistics
* Recent RFID scan monitoring
* IoT device online/offline monitoring
* Device heartbeat monitoring
* User authentication
* Session and token-based authentication
* Employee profile images
* PostgreSQL database integration
* LCD feedback on the RFID device
* Buzzer feedback after RFID scans
* Time synchronization
* IoT device fail-safe handling

## System Architecture

```text
                    ┌─────────────────────┐
                    │     RFID Card       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    IoT RFID Device  │
                    │                     │
                    │  RFID Reader        │
                    │  LCD Display        │
                    │  Buzzer             │
                    │  MicroPython        │
                    └──────────┬──────────┘
                               │
                         HTTP Requests
                               │
                               ▼
                    ┌─────────────────────┐
                    │      Flask API      │
                    │                     │
                    │ Authentication      │
                    │ RFID Processing     │
                    │ Attendance          │
                    │ Device Monitoring   │
                    └──────────┬──────────┘
                               │
                     ┌─────────┴─────────┐
                     │                   │
                     ▼                   ▼
          ┌──────────────────┐  ┌──────────────────┐
          │   PostgreSQL     │  │   Web Dashboard  │
          │                  │  │                  │
          │ Employees        │  │ HTML             │
          │ Attendance Data  │  │ CSS              │
          │ User Data        │  │ JavaScript       │
          └──────────────────┘  └──────────────────┘
```

## Project Structure

```text
tapin-rfid-attendance-system/
│
├── api/
│   ├── database/
│   ├── app.py
│   ├── requirements.txt
│   └── vercel.json
│
├── database/
│   └── tapin-employee-table.sql
│
├── iot/
│   ├── configs/
│   ├── firmware/
│   ├── boot.py
│   ├── driver.py
│   ├── main.py
│   └── version.txt
│
├── web/
│   ├── css/
│   ├── icon/
│   ├── img/
│   ├── js/
│   ├── pages/
│   ├── index.html
│   ├── login.html
│   ├── package.json
│   └── vercel.json
│
├── LICENSE
└── version.txt
```

## Technologies

### Frontend

* HTML5
* CSS3
* JavaScript
* Static web hosting

### Backend

* Python
* Flask
* REST API
* Session authentication
* Token-based authentication

### Database

* PostgreSQL

### IoT

* MicroPython
* RFID reader
* LCD display
* Buzzer
* Wi-Fi
* HTTP communication

## Web Application

The `web` directory contains the frontend interface for TAP-IN.

It includes:

* Landing page
* Login page
* Dashboard
* Employee management
* Attendance monitoring
* RFID management
* Device monitoring
* JavaScript functionality
* CSS styles
* Images and icons

## API

The `api` directory contains the Flask backend responsible for communication between the web application, database, and IoT RFID devices.

The API handles:

* User authentication
* Login and logout
* Authentication verification
* Dashboard statistics
* Attendance records
* Latest RFID scans
* Device status
* Device heartbeat
* RFID processing
* Database operations
* Device availability checking

Example endpoints include:

```text
/api/logout
/api/get-latest-rfid
/api/get-device-status
/api/dashboard-stats
/api/dashboard-data
/api/attendance
/api/check-device/<device_id>
/api/reload-db
/api/device-ping
```

As of v0.3, the API server has been updated with improved RFID attendance processing and enhanced device monitoring capabilities.

## PostgreSQL Database

TAP-IN uses **PostgreSQL** as its primary database system.

The database stores information required by the attendance system, including employee information, RFID identifiers, attendance records, user accounts, and device-related information.

The database-related files are located in:

```text
database/
api/database/
```

## IoT Device

The `iot` directory contains the MicroPython firmware for the TAP-IN RFID attendance device.

The IoT device is responsible for:

1. Initializing the hardware.
2. Connecting to the configured network.
3. Synchronizing the device time.
4. Initializing the RFID reader.
5. Initializing the LCD.
6. Initializing the buzzer.
7. Sending heartbeat information to the API.
8. Waiting for an RFID card.
9. Reading the RFID UID.
10. Sending the RFID UID to the API.
11. Receiving the attendance result.
12. Displaying the result on the LCD.
13. Providing buzzer feedback.
14. Continuing to monitor for new RFID scans.

## RFID Attendance Flow

```text
Employee
   │
   ▼
Tap RFID Card
   │
   ▼
RFID Reader
   │
   ▼
IoT Device Reads UID
   │
   ▼
Send UID to Flask API
   │
   ▼
API Identifies Employee
   │
   ├───────────────┐
   │               │
   ▼               ▼
Registered       Not Registered
   │               │
   ▼               ▼
Record          Reject /
Attendance      Return Error
   │
   ▼
Update PostgreSQL
   │
   ▼
Update Dashboard
   │
   ▼
Display Result
```

## Device Monitoring

The IoT device periodically communicates with the Flask API through a heartbeat mechanism.

The system can use the heartbeat information to determine whether a device is:

* Online
* Offline
* Active
* Not responding

This allows administrators to monitor connected RFID attendance devices from the web dashboard.

## Attendance Monitoring

The web dashboard provides attendance information collected from RFID scans.

Attendance records can be used for:

* Daily attendance monitoring
* Employee attendance tracking
* DTR monitoring
* RFID scan history
* Attendance verification

## Authentication

TAP-IN includes authentication for protected system functionality.

The API supports authentication mechanisms for accessing protected resources.

Protected functionality may include:

* Dashboard data
* Attendance information
* Employee information
* RFID information
* Device information
* Administrative operations

## Version Timeline

| Version         | Status  | Description                                                                                                                                                  |
| --------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Beta v0.0.0** | Beta    | Initial TAP-IN RFID Attendance System development version. Basic project structure, web interface, API, database integration, and IoT development initiated. |
| **Test v0.1.0** | Testing | Initial IoT device testing version. RFID reader, LCD, buzzer, device communication, and basic RFID attendance functionality tested.                          |
| **Test v0.2.0** | Testing | Web version developed and tested                                                                                                                             |
| **Test v0.3.0** | Testing | API server updates and improvements to RFID attendance processing.                                                                                           |
| **Test v0.4.0** | Testing | Face recognition system integration and testing                                                                                                              |

### Beta v0.0.0

The first development stage of TAP-IN.

Focus:

* Initial system architecture
* Initial web interface
* Flask API development
* PostgreSQL integration
* Initial IoT development
* Initial RFID functionality

### Test v0.1.0 — IoT Device

The first testing milestone focused on the physical RFID attendance device.

Focus:

* RFID reader testing
* RFID card detection
* RFID UID reading
* LCD display testing
* Buzzer testing
* Wi-Fi connectivity
* API communication
* Device heartbeat
* Basic attendance scan testing

### Test v0.2.0 — Web Version

The web version of the TAP-IN system was developed and tested.

Focus:

* Web dashboard development
* Frontend implementation
* User interface design
* Integration with Flask API
* Testing of web functionalities

### Test v0.3.0 — API Server Update

This version focused on updating the API server with:

* Improved RFID attendance processing logic
* Enhanced device monitoring and heartbeat handling
* Bug fixes in attendance recording
* Optimized database queries for dashboard statistics
* Added error handling for device communication timeouts

## Future Improvements

Planned improvements may include:

* Attendance reports
* CSV/PDF attendance export
* Advanced attendance analytics
* Multiple RFID device support
* Improved device management
* Role-based access control
* Password reset
* Automated database backups
* Notification system
* API documentation
* Mobile application
* Real-time updates
* Advanced attendance filtering
* Schedule management
* Holiday management

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See the `LICENSE` file for the complete license terms.

## Author

**lolenseu**

TAP-IN RFID Attendance System — an IoT-enabled attendance management platform integrating RFID hardware, a Flask API, PostgreSQL, and a web dashboard.