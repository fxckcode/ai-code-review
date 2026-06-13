// 🚩 Este archivo tiene problemas deliberados para probar AI Code Review

var API_URL = "https://api.example.com"; // should be const
var TIMEOUT = 5000; // should be const

function fetchUserData(userId) {
  console.log("Fetching user:", userId); // debug log left in production

  var data = null; // unused variable
  var url = API_URL + "/users/" + userId;

  // Promise chain instead of async/await
  return fetch(url)
    .then(function (response) {
      return response.json();
    })
    .then(function (json) {
      if (json) {
        // Nested ternary - hard to read
        var role =
          json.role === "admin"
            ? "Administrator"
            : json.role === "mod"
              ? "Moderator"
              : json.role === "user"
                ? "User"
                : "Unknown";
        return {
          id: userId,
          name: json.name,
          email: json.email,
          role: role,
        };
      }
    })
    .catch(function (err) {
      console.log(err); // silent catch, no rethrow
    });
}

function processItems(items) {
  var results = [];
  // for loop instead of map/filter
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (item.active == true) {
      // == instead of ===
      var processed = transformItem(item);
      results.push(processed);
    }
  }
  return results;
}

function transformItem(item) {
  // Magic number 1.21
  var result = item.value * 1.21;
  // No type validation
  return result;
}

// Function does too many things
function saveUserAndNotify(userData, emailService, db) {
  // No validation of inputs
  db.query("INSERT INTO users (name, email) VALUES ('" + userData.name + "', '" + userData.email + "')"); // SQL injection
  emailService.send(userData.email, "Welcome!", "Thanks for joining " + userData.name);
  console.log("User saved:", userData.name);
  return true;
}

module.exports = {
  fetchUserData: fetchUserData,
  processItems: processItems,
  transformItem: transformItem,
  saveUserAndNotify: saveUserAndNotify,
};
