using UnityEngine;
using Unity.Robotics.ROSTCPConnector;
using RosMessageTypes.Sensor;
using System;

public class RosCameraPublisher : MonoBehaviour
{
    ROSConnection ros;

    public Camera cam;
    public RenderTexture renderTexture;

    public string topicName = "/camera/image_raw";

    Texture2D texture2D;

    void Start()
    {
        ros = ROSConnection.GetOrCreateInstance();

        ros.RegisterPublisher<ImageMsg>(topicName);

        texture2D = new Texture2D(
            renderTexture.width,
            renderTexture.height,
            TextureFormat.RGB24,
            false
        );
    }

    void Update()
    {
        RenderTexture currentRT = RenderTexture.active;
        RenderTexture.active = renderTexture;

        cam.Render();

        texture2D.ReadPixels(
            new Rect(0, 0, renderTexture.width, renderTexture.height),
            0,
            0
        );

        texture2D.Apply();

        byte[] imageBytes = texture2D.GetRawTextureData();

        ImageMsg imageMsg = new ImageMsg();

        imageMsg.height = (uint)renderTexture.height;
        imageMsg.width = (uint)renderTexture.width;
        imageMsg.encoding = "rgb8";
        imageMsg.step = (uint)(renderTexture.width * 3);
        imageMsg.data = imageBytes;

        ros.Publish(topicName, imageMsg);

        RenderTexture.active = currentRT;
    }
}